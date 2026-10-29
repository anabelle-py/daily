from questions_config import EXERCISE_QUESTIONS, LECTURE_QUESTIONS, PERSONAL_LEARN_QUESTIONS, \
    PHYSICAL_TRAINING_QUESTIONS

REVIEW_TYPE_TO_QUESTIONS = {
    'ע"ע': EXERCISE_QUESTIONS,
    'הרצאה': LECTURE_QUESTIONS,
    'ל"ע': PERSONAL_LEARN_QUESTIONS,
    'א"ג': PHYSICAL_TRAINING_QUESTIONS,
}

HIVES_CONFIG = {
    "noam-hive": {
        "username": "admin",
        "password": "F7L3c>B7Rz",
        "url": "https://hadasimhive.israelcentral.cloudapp.azure.com"
    }
}

MISUV_SUBJECT_SYMBOL = "מ''י"
MISUV_SUBJECT_NAME = "משוב יומי"
# Optional override for field `groups` if not inferred; default is [] (PROD UI). Example: [42]
FIELD_GROUP_IDS_FALLBACK = None
# Week-based modules: one module per week. Hive `order` = week index (1..WEEK_MODULE_COUNT).
WEEK_MODULE_COUNT = 60
# Display name for each module in Hive (week number is {0}).
MISUV_WEEK_MODULE_LABEL_TEMPLATE = "שבוע {}"
# Optional final text field when includeShareQuestion is true (must match UI copy).
SHARE_QUESTION_TEXT = "משהו שחשוב לי לשתף"
