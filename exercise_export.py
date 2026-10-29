import json
import logging
from io import BytesIO
from time import perf_counter
from urllib.parse import urlencode, urljoin, urlsplit

from openpyxl import Workbook
from requests import RequestException

HIVE_REQUEST_TIMEOUT = (10, 30)
logger = logging.getLogger(__name__)


def _terminal_log(message, *args):
    print(message % args if args else message, flush=True)


class ExerciseNotFoundError(LookupError):
    pass


class AmbiguousExerciseError(ValueError):
    pass


class HiveResponseError(RuntimeError):
    pass


def _hive_get(session, url, headers, params=None):
    parsed_url = urlsplit(url)
    query = parsed_url.query
    additional_query = urlencode(params, doseq=True) if params else ""
    if additional_query:
        query = f"{query}&{additional_query}" if query else additional_query
    endpoint = f"{parsed_url.path}?{query}" if query else parsed_url.path
    started_at = perf_counter()
    _terminal_log("Hive GET starting: %s", endpoint)
    try:
        response = session.get(
            url,
            headers=headers,
            params=params,
            timeout=HIVE_REQUEST_TIMEOUT,
        )
    except RequestException as exc:
        elapsed = perf_counter() - started_at
        _terminal_log(
            "Hive GET failed: %s after %.3fs (%s)",
            endpoint,
            elapsed,
            type(exc).__name__,
        )
        logger.warning(
            "Hive GET %s failed after %.3fs (%s)",
            endpoint,
            elapsed,
            type(exc).__name__,
        )
        raise

    elapsed = perf_counter() - started_at
    if response.status_code >= 400:
        logger.warning(
            "Hive GET %s returned status=%s after %.3fs",
            endpoint,
            response.status_code,
            elapsed,
        )
    try:
        response.raise_for_status()
    except RequestException as exc:
        _terminal_log(
            "Hive GET failed: %s status=%s after %.3fs (%s)",
            endpoint,
            response.status_code,
            elapsed,
            type(exc).__name__,
        )
        raise

    _terminal_log(
        "Hive GET complete: %s status=%s in %.3fs",
        endpoint,
        response.status_code,
        elapsed,
    )
    logger.info(
        "Hive GET %s returned status=%s in %.3fs",
        endpoint,
        response.status_code,
        elapsed,
    )
    return response


def _get_all_list(session, url, hive_host, headers, params=None):
    results = []
    current_url = url
    current_params = params
    visited_urls = set()
    expected_origin = urlsplit(hive_host)

    while current_url:
        response = _hive_get(session, current_url, headers, params=current_params)
        payload = response.json()
        if isinstance(payload, list):
            results.extend(payload)
            break
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise HiveResponseError("Hive returned an unexpected list response")
        results.extend(payload["results"])

        next_page = payload.get("next")
        if not next_page:
            break
        current_url = urljoin(current_url, next_page)
        next_origin = urlsplit(current_url)
        if (next_origin.scheme, next_origin.netloc.lower()) != (
            expected_origin.scheme,
            expected_origin.netloc.lower(),
        ):
            raise HiveResponseError("Hive returned a pagination link outside its configured host")
        if current_url in visited_urls:
            raise HiveResponseError("Hive returned a repeated pagination link")
        visited_urls.add(current_url)
        current_params = None

    return results


def _get_response_payload(session, url, hive_host, headers):
    first_page = _hive_get(session, url, headers)
    payload = first_page.json()
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise HiveResponseError("Hive returned an unexpected response payload")
    if not isinstance(payload.get("results"), list):
        return payload

    results = list(payload["results"])
    next_page = payload.get("next")
    expected_origin = urlsplit(hive_host)
    visited_urls = {url}
    current_url = url
    while next_page:
        page_url = urljoin(current_url, next_page)
        page_origin = urlsplit(page_url)
        if (page_origin.scheme, page_origin.netloc.lower()) != (
            expected_origin.scheme,
            expected_origin.netloc.lower(),
        ):
            raise HiveResponseError("Hive returned a pagination link outside its configured host")
        if page_url in visited_urls:
            raise HiveResponseError("Hive returned a repeated pagination link")
        visited_urls.add(page_url)
        page = _hive_get(session, page_url, headers)
        page_payload = page.json()
        if not isinstance(page_payload, dict) or not isinstance(page_payload.get("results"), list):
            raise HiveResponseError("Hive returned an unexpected paginated response")
        results.extend(page_payload["results"])
        current_url = page_url
        next_page = page_payload.get("next")
    return results


def _field_value(field):
    if isinstance(field, dict):
        return field.get("id", field.get("pk")), field.get("name")
    return field, None


def _field_columns(fields):
    columns = []
    field_names = {}
    field_ids = {}
    ignored_field_ids = set()
    name_counts = {}
    for field in fields:
        field_id, field_name = _field_value(field)
        if isinstance(field, dict) and field.get("has_value") is False:
            if field_id is not None:
                ignored_field_ids.add(str(field_id))
            continue
        if field_name is None:
            field_name = f"Field {field_id}"
        name_counts[field_name] = name_counts.get(field_name, 0) + 1
        column_name = field_name
        if name_counts[field_name] > 1:
            column_name = f"{field_name} ({name_counts[field_name]})"
        columns.append(column_name)
        if field_id is not None:
            field_ids[str(field_id)] = column_name
        field_names[field_name] = column_name
    return columns, field_ids, field_names, ignored_field_ids


def _response_value(entry):
    for key in ("value", "answer", "response", "text", "content"):
        if key in entry:
            value = entry[key]
            if isinstance(value, dict) and "value" in value:
                return value["value"]
            return value
    return None


def _collect_answers(payload, field_ids, field_names, ignored_field_ids, answers):
    if isinstance(payload, list):
        for item in payload:
            _collect_answers(item, field_ids, field_names, ignored_field_ids, answers)
        return
    if not isinstance(payload, dict):
        return

    field_ref = payload.get(
        "field_id",
        payload.get("field_pk", payload.get("field", payload.get("question_id"))),
    )
    field_id, field_name = _field_value(field_ref)
    if field_id is not None or field_name is not None:
        if str(field_id) in ignored_field_ids:
            return
        column = field_ids.get(str(field_id)) if field_id is not None else None
        if column is None and isinstance(field_id, str):
            column = field_names.get(field_id)
        if column is None and field_name is not None:
            column = field_names.get(field_name, field_name)
        if column is None:
            column = f"Field {field_id}"
        value = _response_value(payload)
        if value is not None:
            answers.setdefault(column, []).append(value)
        return

    for key, value in payload.items():
        if key in field_ids:
            answers.setdefault(field_ids[key], []).append(value)
        elif key in ignored_field_ids:
            continue
        elif key in field_names:
            answers.setdefault(field_names[key], []).append(value)
        elif isinstance(value, (list, dict)):
            _collect_answers(value, field_ids, field_names, ignored_field_ids, answers)


def _student_name(*payloads):
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("user_name", "username", "student_name", "student", "user"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                for name_key in ("username", "user_name", "name", "full_name"):
                    name = value.get(name_key)
                    if name:
                        return str(name)
                first = value.get("first_name", "")
                last = value.get("last_name", "")
                if first or last:
                    return f"{first} {last}".strip()
        for key in ("name", "full_name", "display_name"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        first = payload.get("first_name", "")
        last = payload.get("last_name", "")
        if first or last:
            return f"{first} {last}".strip()
    return None


def _assignment_user_id(assignment):
    user = assignment.get("user", assignment.get("user_id"))
    if isinstance(user, dict):
        user = user.get("id", user.get("pk"))
    if isinstance(user, (str, int)) and str(user).isdigit():
        return str(user)
    return None


def _users_by_id(hive_api, assignments):
    needed_user_ids = {
        user_id
        for assignment in assignments
        if _student_name(assignment) is None
        for user_id in [_assignment_user_id(assignment)]
        if user_id is not None
    }
    if not needed_user_ids:
        return {}

    _terminal_log(
        "Exercise responses: loading user names for %d assignment users",
        len(needed_user_ids),
    )
    users = _get_all_list(
        hive_api.session,
        f"{hive_api.hive_host}/api/core/management/users/",
        hive_api.hive_host,
        hive_api.headers,
    )
    return {
        str(user.get("id", user.get("pk"))): _student_name(user)
        for user in users
        if str(user.get("id", user.get("pk"))) in needed_user_ids
        and _student_name(user) is not None
    }


def _cell_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _answer_cell(values):
    if not values:
        return None
    if len(values) == 1:
        return _cell_value(values[0])
    return _cell_value("; ".join(str(value) for value in values))


def _find_exercise(hives, module_name, exercise_name):
    matches = []
    module_found = False
    for hive_api in hives:
        modules = _get_all_list(
            hive_api.session,
            f"{hive_api.hive_host}/api/core/course/modules/?parent_subject__id=4",
            hive_api.hive_host,
            hive_api.headers,
        )
        for module in modules:
            if module.get("name") != module_name:
                continue
            module_found = True
            exercises = _get_all_list(
                hive_api.session,
                f"{hive_api.hive_host}/api/core/course/exercises",
                hive_api.hive_host,
                hive_api.headers,
                params={"parent_module__id": module["id"]},
            )
            for exercise in exercises:
                if exercise.get("name") == exercise_name:
                    matches.append((hive_api, exercise))

    if not module_found:
        raise ExerciseNotFoundError(f"Module '{module_name}' was not found")
    if not matches:
        raise ExerciseNotFoundError(
            f"Exercise '{exercise_name}' was not found in module '{module_name}'"
        )
    if len(matches) > 1:
        raise AmbiguousExerciseError(
            f"More than one exercise named '{exercise_name}' was found in module '{module_name}'"
        )
    return matches[0]


def exercise_exists(hives, module_name, exercise_name):
    try:
        _find_exercise(hives, module_name, exercise_name)
    except ExerciseNotFoundError:
        return False
    except AmbiguousExerciseError:
        return True
    return True


def get_exercise_responses(hives, module_name, exercise_name):
    _terminal_log(
        "Exercise responses: looking up module=%r exercise=%r",
        module_name,
        exercise_name,
    )
    hive_api, exercise = _find_exercise(hives, module_name, exercise_name)
    exercise_id = exercise["id"]
    _terminal_log("Exercise responses: found exercise_id=%s", exercise_id)

    _terminal_log("Exercise responses: loading fields")
    fields = _get_all_list(
        hive_api.session,
        f"{hive_api.hive_host}/api/core/course/exercises/{exercise_id}/fields/",
        hive_api.hive_host,
        hive_api.headers,
    )
    columns, field_ids, field_names, ignored_field_ids = _field_columns(fields)
    _terminal_log("Exercise responses: loaded %d fields", len(fields))

    _terminal_log("Exercise responses: loading assignments")
    assignments = _get_all_list(
        hive_api.session,
        f"{hive_api.hive_host}/api/core/assignments/",
        hive_api.hive_host,
        hive_api.headers,
        params={"exercise__id": exercise_id},
    )

    _terminal_log("Exercise responses: loaded %d assignments", len(assignments))
    user_names = _users_by_id(hive_api, assignments)

    responses = []
    for index, assignment in enumerate(assignments, start=1):
        assignment_id = assignment["id"]
        _terminal_log(
            "Exercise responses: fetching assignment %d/%d id=%s",
            index,
            len(assignments),
            assignment_id,
        )
        response_payload = _get_response_payload(
            hive_api.session,
            f"{hive_api.hive_host}/api/core/assignments/{assignment_id}/responses",
            hive_api.hive_host,
            hive_api.headers,
        )
        answers = {}
        _collect_answers(response_payload, field_ids, field_names, ignored_field_ids, answers)
        student_name = _student_name(assignment, response_payload)
        if student_name is None:
            user_id = _assignment_user_id(assignment)
            student_name = user_names.get(user_id)
        responses.append(
            {
                "student": student_name or f"Unknown student (assignment {assignment_id})",
                "assignmentId": assignment_id,
                "responses": answers,
            }
        )
        _terminal_log(
            "Exercise responses: parsed assignment %d/%d id=%s",
            index,
            len(assignments),
            assignment_id,
        )
    _terminal_log("Exercise responses: complete, returning %d responses", len(responses))
    return columns, responses


def create_exercise_responses_workbook(hives, module_name, exercise_name):
    columns, responses = get_exercise_responses(hives, module_name, exercise_name)
    extra_columns = [
        column
        for response in responses
        for column in response["responses"]
        if column not in columns
    ]
    extra_columns = list(dict.fromkeys(extra_columns))

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Responses"
    worksheet.append(
        ["Student", "Assignment ID", *[_cell_value(column) for column in [*columns, *extra_columns]]]
    )
    for response in responses:
        worksheet.append(
            [
                _cell_value(response["student"]),
                response["assignmentId"],
                *[
                    _answer_cell(response["responses"].get(column, []))
                    for column in [*columns, *extra_columns]
                ],
            ]
        )

    worksheet.freeze_panes = "C2"
    worksheet.auto_filter.ref = worksheet.dimensions

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
