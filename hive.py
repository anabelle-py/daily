import os

import requests

import urllib3
from urllib3.exceptions import InsecureRequestWarning

urllib3.disable_warnings(InsecureRequestWarning)

STUDENT_NAME = "Student (id) - Hanich{id}"


def hive_fields_debug_enabled() -> bool:
    """True if DEBUG_HIVE_FIELDS or DEBUG_HIVE is set — use for exercise field POST/GET tracing."""
    def on(name: str) -> bool:
        return os.environ.get(name, "").lower() in ("1", "true", "yes")

    return on("DEBUG_HIVE_FIELDS") or on("DEBUG_HIVE")


def hive_fields_log(msg: str, **kwargs) -> None:
    if not hive_fields_debug_enabled():
        return
    extra = " | ".join(f"{k}={v!r}" for k, v in kwargs.items())
    print(f"[Hive FIELDS] {msg}" + (f" | {extra}" if extra else ""))


def _unwrap_list(data):
    """Hive may return a bare list or DRF pagination: {count, next, results: [...]}."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return []


class HiveAPI:
    def __init__(self, username, password, hive_host):
        self.hive_host = hive_host
        self.session = requests.session()
        # Hive may use a self-signed cert; verify all requests consistently.
        self.session.verify = False
        self.token = self.login(username, password)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def login(self, username, password):
        """
        Login to server
        @param username: The username to log in with
        @param password: The password to log in with
        """
        cred = {"username": username, "password": password}
        response = self.session.get(self.hive_host + "/api/auth/session")
        if response.status_code == 200:
            response = self.session.post(self.hive_host + "/api/core/token/", json=cred)
        if response.status_code == 200:
            return response.json()["access"]
        return False

    def add_user(self, username, password, number=1, gender="Female", program=1, clearance=1, checkers_brief="",
                 status="Present", email="", first_name="", last_name="", queue=None, user_queue=None,
                 permitted_programs=None):
        """"
        Adds a new user
        @param username: The user's username
        @param password: The user's password @return http response
        """
        if permitted_programs is None:
            permitted_programs = []
        body = {
            "username": username,
            "gender": gender,
            "number": number,
            "program": program,
            "clearance": clearance,
            "checkers_brief": checkers_brief,
            "status": status,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "queue": queue,
            "user_queue": user_queue,
            "password": password,
            "permitted_programs": permitted_programs
        }
        return self.session.post(self.hive_host + "/api/core/management/users/", json=body,
                                 headers=self.headers)

    def create_module(self, subject, module, order, rolling):
        """
        Add a new module
        @param module: The module's name
        @param subject: The name of the module's subject
        @param order: The module order in all module list
        @return http response
        """
        body = {
            "name": module,
            "parent_subject": subject,
            "order": order,
            "enable_queues_on_assign": rolling
        }
        return self.session.post(self.hive_host + "/api/core/course/modules/", json=body, headers=self.headers)

    def create_exercise(self, module_id, exercise, order, download=True, patbas="Never", preview=True,
                        preview_display=None, tags=None):
        """
        Add a new exercise.
        UI captures: `preview` is the exercise preview mode ("Disabled", "Markdown", …);
        `patbas_preview` stays "Disabled" independently (see Hive Network tab).
        """
        if tags is None:
            tags = []
        if preview_display is not None:
            preview_value = preview_display
        else:
            # Default: False -> Disabled; True -> Markdown (only other captured mode).
            preview_value = "Disabled" if not preview else "Markdown"
        body = {
            "parent_module": module_id,
            "name": exercise,
            "order": str(order),
            "patbas": patbas,
            "preview": preview_value,
            "patbas_preview": "Disabled",
            "patbas_download": download,
            "download": download,
            "autodone": False,
            "tags": tags,
            "on_creation_data": {},
            "expected_duration": None,
            "autocheck_tag": "",
            "is_lecture": False,
            "style": "",
            "segel_brief": "",
        }
        response = self.session.post(self.hive_host + "/api/core/course/exercises/", json=body,
                                 headers=self.headers)
        return response

    def read_field_group_ids_from_exercise(self, exercise_id):
        """
        Return field-group primary keys from an existing exercise field (e.g. auto "Comment"), if any.
        If all existing fields use `groups: []`, returns None and create_field uses [] (matches PROD UI).
        """
        response = self.session.get(
            self.hive_host + "/api/core/course/exercises/{}/fields/".format(exercise_id),
            headers=self.headers,
        )
        if not response.ok:
            hive_fields_log(
                "read_field_group_ids_from_exercise failed",
                exercise_id=exercise_id,
                status=response.status_code,
            )
            return None
        for field in _unwrap_list(response.json()):
            raw = field.get("groups")
            if not raw:
                continue
            if isinstance(raw[0], dict):
                ids = [int(x["id"]) for x in raw if x.get("id") is not None]
            else:
                ids = [int(x) for x in raw]
            if ids:
                hive_fields_log("read_field_group_ids_from_exercise", exercise_id=exercise_id, groups=ids)
                return ids
        return None

    def create_field(self, exercise_id, name, metadata=None, has_value=True,
                     required=False, segel_only=True, type="text", for_response_type=None,
                     lower_limit=None, upper_limit=None, order=999, description="",
                     choices=None, groups=None, hanich_responses=None, staff_responses=None):
        """
        Creates a new field in a given exercise.
        Newer Hive uses hanich_responses/staff_responses (not for_response_type); for_response_type is ignored.
        """
        if metadata is None:
            metadata = {}
        if choices is None:
            choices = []
        if groups is None:
            groups = getattr(self, "_misuv_resolved_field_groups", None)
        if groups is None:
            # PROD UI sends `groups: []`; pk 1 often does not exist. Empty list is valid (see field POST capture).
            groups = []
        if hanich_responses is None and staff_responses is None:
            if segel_only:
                hanich_responses, staff_responses = False, True
            else:
                hanich_responses, staff_responses = True, False
        else:
            if hanich_responses is None:
                hanich_responses = False
            if staff_responses is None:
                staff_responses = False

        body = {
            "name": name,
            "segel_only": segel_only,
            "required": required,
            "hanich_responses": hanich_responses,
            "staff_responses": staff_responses,
            "type": type,
            "order": order,
            "has_value": has_value,
            "description": description,
            "metadata": metadata,
            "lower_limit": lower_limit,
            "upper_limit": upper_limit,
            "choices": choices,
            "groups": groups,
        }
        url = f"{self.hive_host}/api/core/course/exercises/{exercise_id}/fields/"
        hive_fields_log(
            "create_field request",
            exercise_id=exercise_id,
            name_preview=name[:120] if isinstance(name, str) else name,
            type=type,
            has_value=has_value,
            required=required,
            segel_only=segel_only,
            order=order,
            lower_limit=lower_limit,
            upper_limit=upper_limit,
            hanich_responses=hanich_responses,
            staff_responses=staff_responses,
            groups=groups,
        )
        response = self.session.post(url, json=body, headers=self.headers)
        snippet = response.text[:3000] if response.text else ""
        hive_fields_log(
            "create_field response",
            exercise_id=exercise_id,
            status=response.status_code,
            content_length=len(response.content or b""),
        )
        if hive_fields_debug_enabled():
            try:
                parsed = response.json()
            except Exception:
                parsed = snippet
            hive_fields_log("create_field response_body", exercise_id=exercise_id, body=parsed)
        return response

    def get_help_responses(self, help_id):
        response = self.session.get(self.hive_host + f"/api/core/help/{help_id}/responses/", headers=self.headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"help (help_id) does not exist")
            return None

    def get_hanich_helps(self, student_number):
        response = self.session.get(self.hive_host + "/api/core/help/", headers=self.headers)
        helps_list = list(
            filter(lambda a_help: a_help["user_name"] == STUDENT_NAME.format(id=student_number),
                   _unwrap_list(response.json())))
        if len(helps_list):
            responses = [self.get_help_responses(a_help["id"]) for a_help in helps_list]
            return responses
        else:
            print(f"Couldn't find any helps from student {student_number}")
            return None

    # --- Queue API (disabled; misuv flow only creates exercise + fields) ---
    # def get_queue_id(self, queue_name, module_id):
    #     """
    #     Gets the id of the queue.
    #     @param queue_name: The name of the wanted queue.
    #     """
    #     response = self.session.get(self.hive_host + f"/api/core/queues/", headers=self.headers,
    #                                 params={"for_object__module": module_id})
    #     try:
    #         return list(filter(lambda queue: (queue["name"] == queue_name), _unwrap_list(response.json())))[0]["id"]
    #     except IndexError:
    #         print(f" queue {queue_name} does not exist")
    #         return None
    #
    # def add_exercise_to_queue(self, exercise_id, queue_id, exercise_order=None):
    #     """
    #     Add exercise at the beginning of the queue (Hive UI uses POST with order 0).
    #     Pass exercise_order to use a different position.
    #     """
    #     next_order = 0 if exercise_order is None else exercise_order
    #     body = {
    #         "order": next_order,
    #         "queue_rule": "Wait For Submitted",
    #         "set_nested_queue_id": None,
    #         "set_exercise_id": exercise_id,
    #         "set_module_id": None,
    #         "continue_on_redo": False,
    #         "enabled": True,
    #         "set_tags_id": [],
    #     }
    #     response = self.session.post(f"{self.hive_host}/api/core/queues/{queue_id}/items/", json=body,
    #                              headers=self.headers)
    #     return response
    #
    # def clear_existing_queue(self, queue_id):
    #     """
    #     Clears existing queue exercises
    #
    #     @param queue_id: The id of the queue
    #     @return: True if the action succeded, else False.
    #     """
    #     exercises = self.get_all_exercises_in_queue(queue_id)
    #     for exercise in exercises:
    #         self.delete_exercise_from_queue(queue_id, exercise["id"])
    #
    #     exercises = self.get_all_exercises_in_queue(queue_id)
    #     if exercises:
    #         print(f" queue (queue_id) could not be cleared")
    #         return False
    #     return True
    #
    # def delete_exercise_from_queue(self, queue_id, exercise_queue_id):
    #     """
    #     Deletes exercise from specified queue.
    #     @param queue_id: The id of the queue
    #     @param exercise_queue_id: The id of the exercise in the queue object, NOT THE ID OF THE EXERCISE OBJECT!
    #     @return: True if the action succeded, else False.
    #     """
    #     response = self.session.delete(self.hive_host + f"/api/core/queues/{queue_id}/items/{exercise_queue_id}",
    #                                    headers=self.headers)
    #     if response.status_code == 204:
    #         return True
    #     else:
    #         print(f"Could not delete exercise {exercise_queue_id} from queue {queue_id}")
    #         return False
    #
    # def create_new_queue(self, queue_name, module_id, classes_names=[]):
    #     """
    #     Creates new queue under specified module.
    #     THIS FUNCTION CREATES ONLY QUEUES UNDER MODULE AND CAN'T CREATE OTHER QUEUES
    #
    #     @param queue_name: The name of the new queue
    #     @param subject: The name of the subject that contains the queue
    #     @param module: The name of the module that contains the queue
    #     @param classes_names: List of classes names that the queue contains
    #     @return: response object of the request
    #     """
    #     # TODO: support more queue types (4 is module queue)
    #     # Only module (not user): DB check "for_one_item" requires exactly one queue target.
    #     body = {
    #         "name": queue_name,
    #         "module": module_id,
    #         "queue_type": 4,
    #         "for_classes": [self.get_class_id(a_class) for a_class in classes_names],
    #     }
    #     response = self.session.post(f"{self.hive_host}/api/core/queues/", json=body, headers=self.headers)
    #     return response
    #
    # def update_queue(self, queue_name, module_id, classes_names=[]):
    #     queue_id = self.get_queue_id(queue_name, module_id)
    #     body = {
    #         "name": queue_name,
    #         "module": module_id,
    #         "queue_type": 4,
    #         "for_classes": [self.get_class_id(a_class) for a_class in classes_names],
    #     }
    #     return self.session.patch(f"{self.hive_host}/api/core/queues/{queue_id}/", json=body, headers=self.headers)
    #
    # def create_module_queue(self, queue_name, module_id, exercises_names_and_orders=[], classes_names=[]):
    #     queue_id = self.get_queue_id(queue_name, module_id)
    #     if queue_id:
    #         self.clear_existing_queue(queue_id)
    #     else:
    #         self.create_new_queue(queue_name, module_id)
    #
    #     # Add classes after creating / clearing the queue self.update_queue (queue_name, subject, module,
    #     self.update_queue(queue_name, module_id, classes_names)
    #
    #     # Adding all the exercises
    #     for exercise, order in exercises_names_and_orders:
    #         self.add_exercise_to_queue(module_id, exercise, queue_name, order)
    #
    # def get_all_exercises_in_queue(self, queue_id):
    #     response = self.session.get(self.hive_host + f"/api/core/queues/{queue_id}/items/", headers=self.headers)
    #     if response.status_code == 200:
    #         return [e for e in _unwrap_list(response.json())]
    #     else:
    #         print(f"Could not find any exercises in queue {queue_id}")
    #         return []

    def get_all_exercises_im_module(self, module_id):
        params = {
            "parent_module__id": module_id
        }
        response = self.session.get(self.hive_host + f"/api/core/course/exercises", headers=self.headers, params=params)
        return _unwrap_list(response.json())

    def delete_field(self, exercise_id, field_id):
        present_before = field_id in self.get_all_fields_of_exercise(exercise_id)
        hive_fields_log("delete_field", exercise_id=exercise_id, field_id=field_id, present_before=present_before)
        if present_before:
            response = self.session.delete(
                self.hive_host + "/api/core/course/exercises/{}/fields/{}".format(exercise_id, field_id),
                headers=self.headers)
            hive_fields_log("delete_field response", exercise_id=exercise_id, field_id=field_id,
                            status=response.status_code)
        still_there = field_id in self.get_all_fields_of_exercise(exercise_id)
        if still_there:
            print(f"field {field_id} of excercise {exercise_id} could be deleted")
            hive_fields_log("delete_field still_present", exercise_id=exercise_id, field_id=field_id)
            return False
        return True

    def get_all_fields_of_exercise(self, exercise_id):
        response = self.session.get(self.hive_host + "/api/core/course/exercises/{}/fields/".format(exercise_id),
                                    headers=self.headers)
        if not response.ok:
            hive_fields_log(
                "get_all_fields_of_exercise http_error",
                exercise_id=exercise_id,
                status=response.status_code,
                body_preview=(response.text or "")[:2000],
            )
            return []
        try:
            raw = _unwrap_list(response.json())
            ids = [field["id"] for field in raw]
            hive_fields_log(
                "get_all_fields_of_exercise",
                exercise_id=exercise_id,
                status=response.status_code,
                count=len(ids),
                field_ids=ids,
            )
            if hive_fields_debug_enabled() and len(raw) <= 30:
                names_types = [(f.get("name"), f.get("type")) for f in raw]
                hive_fields_log("get_all_fields_of_exercise names", exercise_id=exercise_id, fields=names_types)
            return ids
        except (IndexError, KeyError, TypeError) as exc:
            hive_fields_log("get_all_fields_of_exercise parse_error", exercise_id=exercise_id, error=repr(exc))
            print(f"exercise {exercise_id} does not exist")
            return []

    def add_user_to_class(self, class_name, usernames, program=1):
        class_id = self.get_class_id(class_name)
        users_ids = [self.get_user_id(username) for username in usernames]

        body = {
            "id": class_id,
            "name": class_name,
            "users": users_ids,
            "program": program,
            "email": f"{class_name}@Amnon.com"
        }

        return self.session.patch(f"{self.hive_host}/api/core/management/classes/{class_id}/", json=body,
                                  headers=self.headers)

    def get_subject_id(self, subject_name):
        response = self.session.get(self.hive_host + "/api/core/course/subjects", headers=self.headers)
        try:
            return list(filter(lambda subject: subject["name"] == subject_name, _unwrap_list(response.json())))[0]["id"]
        except IndexError:
            print(f"subject {subject_name} does not exist")
            return None

    def get_module_id(self, subject_id, module_name):
        params = {
            "parent_subject__id": subject_id,
        }
        response = self.session.get(self.hive_host + "/api/core/course/modules/", headers=self.headers, params=params)
        try:
            return list(filter(lambda module: module["name"] == module_name, _unwrap_list(response.json())))[0]["id"]
        except IndexError:
            print(f"module {module_name} does not exist")
            return None

    def get_exercise_id(self, module_id, exercise_name, order=None):
        response = self.session.get(self.hive_host + "/api/core/course/exercises", headers=self.headers)
        try:
            exercises = list(filter(lambda exercise: (exercise["name"] == exercise_name) and (
                    exercise["parent_module_id"] == module_id),
                                    _unwrap_list(response.json())))
            if order:
                exercises = [e for e in exercises if e["order"] == order]

            return exercises[0]["id"]
        except IndexError:
            print(f"exercise {exercise_name} does not exist")
            return None

    def get_class_id(self, class_name):
        response = self.session.get(self.hive_host + "/api/core/management/classes/", headers=self.headers)
        try:
            return list(filter(lambda a_class: a_class["name"] == class_name, _unwrap_list(response.json())))[0]["id"]
        except IndexError:
            print(f"class {class_name} does not exist")
            return None

    def get_user_id(self, username):
        response = self.session.get(self.hive_host + "/api/core/management/users/", headers=self.headers)
        try:
            return list(filter(lambda users: users["username"] == username, _unwrap_list(response.json())))[0]["id"]
        except IndexError:
            print(f"user {username} does not exist")
            return None

    def create_subject(self, program_id, symbol, name, color):
        body = {
            "name": name,
            "parent_program": program_id,
            "symbol": symbol,
            "color": color
        }
        response = self.session.post(self.hive_host + "/api/core/course/subjects/", json=body, headers=self.headers)
        return response

    def retrieve_exercise_answers_by_id(self, exercise_id):
        response = self.session.get(self.hive_host + "/api/core/assignments/?exercise_id={}".format(exercise_id),
                                    headers=self.headers)
        assignment_ids = [assignment["id"] for assignment in _unwrap_list(response.json())]
        return self.get_responses_by_ids(assignment_ids)

    def get_responses_by_ids(self, assingment_ids):
        return [self.session.get(self.hive_host + "/api/core/assignments/{}/responses".format(id),
                                 headers=self.headers)
                for id in assingment_ids]

    def retrieve_exercise_fields_by_id(self, exercise_id):
        response = self.session.get(self.hive_host + "/api/core/course/exercises/{}/fields/".format(str(exercise_id)),
                                    headers=self.headers)
        return _unwrap_list(response.json())

    def update_field(self, exercise_id, field_id, field_data: dict):
        return self.session.patch(self.hive_host +
                                  f"/api/core/course/exercises/{exercise_id}/fields/{field_id}/",
                                  headers=self.headers,
                                  json=field_data)

    def get_programs_names(self):
        response = self.session.get(self.hive_host + "/api/core/course/programs/", headers=self.headers)
        programs = _unwrap_list(response.json())
        names = [program["name"] for program in programs]
        return names

    def get_program_id_by_name(self, program_name):
        response = self.session.get(self.hive_host + "/api/core/course/programs/", headers=self.headers)
        programs = _unwrap_list(response.json())
        program_ids = [program["id"] for program in programs if program["name"] == program_name]
        assert any(program_ids), f"No Program names {program_name}"
        return program_ids[0]

    def get_subject_id_in_program(self, subject_symbol, program_name):
        program_id = self.get_program_id_by_name(program_name)
        params = {
            "parent_program__id": program_id,
        }
        response = self.session.get(self.hive_host + "/api/core/course/subjects/", headers=self.headers, params=params)
        subjects = _unwrap_list(response.json())
        try:
            subject = [s for s in subjects if s["symbol"] == subject_symbol][0]
            return subject["id"]
        except IndexError:
            print(f"Subject with symbol {subject_symbol} does not exist")
            return None
