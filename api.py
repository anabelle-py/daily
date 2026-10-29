import re
from io import BytesIO
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from requests import RequestException

import config
from api_types import DailySubmission
from exercise_export import (
    AmbiguousExerciseError,
    ExerciseNotFoundError,
    HiveResponseError,
    create_exercise_responses_workbook,
    exercise_exists,
    get_exercise_responses,
)
from hive import HiveAPI
from misuv_creator import MisuvCreator

app = FastAPI()
hives_api = {hive_name: HiveAPI(hive_config.get("username"), hive_config.get("password"), hive_config.get("url")) for
             hive_name, hive_config in config.HIVES_CONFIG.items()}

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# @app.get("/api/exercise-exists")
# async def check_exercise_exists(moduleName: str, exerciseName: str):
#     try:
#         return exercise_exists(hives_api.values(), moduleName, exerciseName)
#     except (RequestException, HiveResponseError) as exc:
#         raise HTTPException(status_code=502, detail="Unable to retrieve exercise data from Hive") from exc


# @app.get("/api/exercise-responses")
# async def get_responses_json(moduleName: str, exerciseName: str):
#     print("[exercise-responses] HTTP request started", flush=True)
#     try:
#         _, responses = await run_in_threadpool(
#             get_exercise_responses,
#             list(hives_api.values()),
#             moduleName,
#             exerciseName,
#         )
#         print(
#             f"[exercise-responses] HTTP request complete; returning {len(responses)} responses",
#             flush=True,
#         )
#         return responses
#     except ExerciseNotFoundError as exc:
#         print(f"[exercise-responses] not found: {exc}", flush=True)
#         raise HTTPException(status_code=404, detail=str(exc)) from exc
#     except AmbiguousExerciseError as exc:
#         print(f"[exercise-responses] ambiguous exercise: {exc}", flush=True)
#         raise HTTPException(status_code=409, detail=str(exc)) from exc
#     except (RequestException, HiveResponseError) as exc:
#         print(f"[exercise-responses] Hive request failed: {type(exc).__name__}", flush=True)
#         raise HTTPException(status_code=502, detail="Unable to retrieve exercise data from Hive") from exc


@app.get("/api/exercise-responses.xlsx")
async def export_exercise_responses(moduleName: str, exerciseName: str):
    try:
        workbook = await run_in_threadpool(
            create_exercise_responses_workbook,
            list(hives_api.values()),
            moduleName,
            exerciseName,
        )
    except ExerciseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AmbiguousExerciseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RequestException, HiveResponseError) as exc:
        raise HTTPException(status_code=502, detail="Unable to retrieve exercise data from Hive") from exc

    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", f"{moduleName}-{exerciseName}")[:150]
    return StreamingResponse(
        BytesIO(workbook),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="exercise-responses.xlsx"; '
                f"filename*=UTF-8''{quote(filename + '.xlsx')}"
            )
        },
    )


# @app.get("/api/courses")
# async def get_courses():
#     courses = []
#     for hive_name, hive_api in hives_api.items():
#         programs = hive_api.get_programs_names()
#         for program in programs:
#             courses.append(f"{hive_name}:{program}")
#     return courses
#

# @app.get("/api/reviewTypes")
# async def get_review_types():
#     return list(config.REVIEW_TYPE_TO_QUESTIONS.keys())
#
#
# @app.get("/api/weeks")
# async def get_week_range():
#     """Week numbers available for מישוב (1 .. WEEK_MODULE_COUNT)."""
#     return {"minWeek": 1, "maxWeek": config.WEEK_MODULE_COUNT}
#
#
# @app.post("/api/submit")
# async def submit_daily(daily_data: DailySubmission):
#     # Do something with the received data
#     # Example: Print the data
#     print("Received Data:")
#     print(f"Course: {daily_data.course}")
#     print(f"Week: {daily_data.weekNumber}")
#     print(f"Date: {daily_data.selectedDate}")
#     print(f"Class Reviews: {daily_data.classReviews}")
#     print(f"Daily Question: {daily_data.dailyQuestion}")
#     print(f"Include share question: {daily_data.includeShareQuestion}")
#
#     if daily_data.weekNumber < 1 or daily_data.weekNumber > config.WEEK_MODULE_COUNT:
#         raise HTTPException(
#             status_code=400,
#             detail=f"Week must be between 1 and {config.WEEK_MODULE_COUNT}",
#         )
#
#     # You can process the data further or save it to a database
#     hive_name, course = daily_data.course.split(":")
#     daily_data.course = course
#     hive_api = hives_api.get(hive_name)
#     misuv_creator = MisuvCreator(hive_api, daily_data)
#     url = misuv_creator.create()
#     exercise_url = f"{hive_api.hive_host}/{url}"
#     # Return a response
#     return {"message": f"קישור למישוב {exercise_url}"}
