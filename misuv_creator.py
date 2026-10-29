import os

import config
from api_types import ClassReview, DailySubmission
from config import REVIEW_TYPE_TO_QUESTIONS
from hive import HiveAPI, hive_fields_log


def _hive_debug_print(what: str, response):
    """Set DEBUG_HIVE=1 in the environment to log full Hive HTTP responses (copy for debugging)."""
    if os.environ.get("DEBUG_HIVE", "").lower() not in ("1", "true", "yes"):
        return
    body = response.text
    print(f"\n{'=' * 60}\n[Hive DEBUG] {what}\nHTTP {response.status_code}\n{body}\n{'=' * 60}\n")


def _require_json_id(response, what: str):
    """Parse JSON from a Hive POST response; raise with body on error or missing id."""
    _hive_debug_print(what, response)
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"{what}: not JSON ({exc}); raw={response.text[:2000]!r}") from exc
    if not response.ok:
        raise RuntimeError(f"{what}: HTTP {response.status_code} {data}")
    if not isinstance(data, dict):
        raise RuntimeError(f"{what}: expected object, got {data!r}")
    rid = data.get("id", data.get("pk"))
    if rid is None:
        raise RuntimeError(f"{what}: missing id/pk in {data!r}")
    return rid


class MisuvCreator():
    def __init__(self, hive_api: HiveAPI, daily_submission: DailySubmission):
        self.hive_api = hive_api
        self.daily_submission = daily_submission

    def _next_field_order(self) -> int:
        """Hive sorts fields by `order`; default 999 on every field breaks sequence."""
        self._field_order_seq += 1
        return self._field_order_seq

    def create(self):
        program_id = self.hive_api.get_program_id_by_name(self.daily_submission.course)
        subject_id = self.hive_api.get_subject_id_in_program(config.MISUV_SUBJECT_SYMBOL, self.daily_submission.course)
        if not subject_id:
            subject_id = _require_json_id(
                self.hive_api.create_subject(program_id, config.MISUV_SUBJECT_SYMBOL,
                                             config.MISUV_SUBJECT_NAME, "#ffeb3b"),
                "create_subject",
            )
        week = self.daily_submission.weekNumber
        module_name = config.MISUV_WEEK_MODULE_LABEL_TEMPLATE.format(week)
        module_order = str(week)
        module_id = self.hive_api.get_module_id(subject_id, module_name)
        if not module_id:
            response = self.hive_api.create_module(subject_id, module_name, module_order, False)
            module_id = _require_json_id(response, "create_module")
        module_exercises = self.hive_api.get_all_exercises_im_module(module_id)
        module_order = 1
        if len(module_exercises) > 0:
            module_order = int(max(self.hive_api.get_all_exercises_im_module(module_id),
                                   key=lambda exercise: int(exercise['order']))["order"]) + 1
        exercise_id = _require_json_id(
            self.hive_api.create_exercise(
                module_id,
                self.daily_submission.selectedDate.strftime("%d.%m.%Y"),
                module_order,
                download=False,
                preview=False,
            ),
            "create_exercise",
        )
        self.create_questions(exercise_id)

        # Queue integration disabled — only create exercise + fields; re-enable in hive.py + here if needed.
        # queue_id = self.hive_api.get_queue_id(module_name, module_id)
        # if not queue_id:
        #     queue_id = _require_json_id(
        #         self.hive_api.create_new_queue(module_name, module_id),
        #         "create_new_queue",
        #     )
        # self.hive_api.add_exercise_to_queue(exercise_id, queue_id)

        # Returning the url for the exercise
        return f"course/{program_id}/{subject_id}/{module_id}/{exercise_id}"

    def create_questions(self, exercise_id):
        # Field `groups`: use ids from an existing field if present; else `[]` (matches PROD UI; avoid hardcoded [1]).
        inferred = self.hive_api.read_field_group_ids_from_exercise(exercise_id)
        fallback = getattr(config, "FIELD_GROUP_IDS_FALLBACK", None)
        if inferred is not None:
            resolved = inferred
            groups_source = "inferred_from_exercise"
        elif fallback is not None:
            resolved = fallback
            groups_source = "config_FIELD_GROUP_IDS_FALLBACK"
        else:
            resolved = []
            groups_source = "default_empty_list"
        self.hive_api._misuv_resolved_field_groups = resolved
        try:
            hive_fields_log(
                "create_questions start",
                exercise_id=exercise_id,
                class_review_count=len(self.daily_submission.classReviews),
                has_daily_question=bool(self.daily_submission.dailyQuestion),
                include_share_question=self.daily_submission.includeShareQuestion,
                field_groups=resolved,
                groups_source=groups_source,
            )
            self.delete_existing_fields(exercise_id)

            self._field_order_seq = 0
            # self.add_separator_field(exercise_id)
            for i, event in enumerate(self.daily_submission.classReviews):
                hive_fields_log("create_questions class_review", exercise_id=exercise_id, index=i,
                                review_type=event.type)
                self.add_questions_for_event(event, exercise_id)

            if self.daily_submission.dailyQuestion:
                self.hive_api.create_field(exercise_id, "השאלה היומית", has_value=False, segel_only=False,
                                           required=False, order=self._next_field_order())
                self.hive_api.create_field(exercise_id, f"{self.daily_submission.dailyQuestion}", type="text",
                                           segel_only=False, required=True, order=self._next_field_order())
                # self.add_separator_field(exercise_id)

            # self.hive_api.create_field(exercise_id, f"הערות כלליות לגבי היום", has_value=False, segel_only=False,
            #                            required=False)
            # self.hive_api.create_field(exercise_id, "הערות כלליות", type="text", segel_only=False, required=False)

            if self.daily_submission.includeShareQuestion:
                # Do not add_separator here: last class review (and daily block if any) already ends with one.
                self.hive_api.create_field(
                    exercise_id,
                    config.SHARE_QUESTION_TEXT,
                    type="text",
                    segel_only=False,
                    required=False,
                    order=self._next_field_order(),
                )

            final_ids = self.hive_api.get_all_fields_of_exercise(exercise_id)
            hive_fields_log("create_questions done", exercise_id=exercise_id, final_field_count=len(final_ids))
        finally:
            self.hive_api.__dict__.pop("_misuv_resolved_field_groups", None)

    def delete_existing_fields(self, exercise_id):
        fields = self.hive_api.get_all_fields_of_exercise(exercise_id)
        hive_fields_log("delete_existing_fields", exercise_id=exercise_id, count=len(fields))
        for field_id in fields:
            self.hive_api.delete_field(exercise_id, field_id)

    def add_questions_for_event(self, review: ClassReview, exercise_id):
        questions = REVIEW_TYPE_TO_QUESTIONS[review.type]
        self.hive_api.create_field(exercise_id, f"{review.type} - {review.title}", has_value=False, segel_only=False,
                                   required=False, order=self._next_field_order())
        for question in questions:
            self.hive_api.create_field(exercise_id, question, type="number", lower_limit=1, upper_limit=10,
                                       segel_only=False, required=True, order=self._next_field_order())
        self.hive_api.create_field(exercise_id, "פירוט", type="text", segel_only=False, required=True,
                                   order=self._next_field_order())
        # self.add_separator_field(exercise_id)

    def add_separator_field(self, exercise_id):
        self.hive_api.create_field(exercise_id,
                                   f"==================================================================================",
                                   has_value=False, segel_only=False,
                                   order=self._next_field_order())
