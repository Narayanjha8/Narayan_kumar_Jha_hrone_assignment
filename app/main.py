import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def configure_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING)])
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    diff_seconds = (punch_in - start).total_seconds()
    if diff_seconds > 600:
        return int(diff_seconds // 60)
    return 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = (punch_out - punch_in).total_seconds()
    return round(seconds / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    diff_seconds = (punch_out - end).total_seconds()
    if diff_seconds >= 1800:
        return int(diff_seconds // 60)
    return 0


class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000)
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100000000000)

class PatchAttendance(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    regularized_by: str
    reason: str


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    
    ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc) if body.punched_at else datetime.now(timezone.utc)
    ts = ts.replace(microsecond=0)
    
    local_ts = ts.astimezone(IST)
    d = local_ts.date().isoformat()
    
    if emp["shift_end"] <= emp["shift_start"]:
        h_end, m_end = map(int, emp["shift_end"].split(":"))
        if local_ts.hour < h_end or (local_ts.hour == h_end and local_ts.minute < m_end):
            d = (local_ts.date() - timedelta(days=1)).isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(local_ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
        doc["id"] = str(res.inserted_id)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    
    doc.pop("_id", None)
    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc) if body.punched_at else datetime.now(timezone.utc)
    ts = ts.replace(microsecond=0)
    
    local_ts = ts.astimezone(IST)
    d = local_ts.date().isoformat()
    
    if emp["shift_end"] <= emp["shift_start"]:
        h_end, m_end = map(int, emp["shift_end"].split(":"))
        if local_ts.hour < h_end or (local_ts.hour == h_end and local_ts.minute < m_end):
            d = (local_ts.date() - timedelta(days=1)).isoformat()

    log = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d})
    if not log or not log.get("punch_in"):
        raise HTTPException(404, "No active punch-in found for this date")
    if log.get("punch_out"):
        raise HTTPException(409, "Already punched out")

    p_in = log["punch_in"]
    wh = compute_work_hours(p_in, ts)
    ot = compute_overtime(local_ts, emp["shift_end"], d)
    hd = wh < 4.50

    update_doc = {
        "punch_out": ts,
        "work_hours": wh,
        "overtime_minutes": ot,
        "half_day": hd
    }
    
    db.attendance_logs.update_one({"_id": log["_id"]}, {"$set": update_doc})
    
    updated = db.attendance_logs.find_one({"_id": log["_id"]})
    updated["id"] = str(updated.pop("_id"))
    if updated.get("punch_in"):
        updated["punch_in"] = int(updated["punch_in"].timestamp() * 1000)
    if updated.get("punch_out"):
        updated["punch_out"] = int(updated["punch_out"].timestamp() * 1000)
    return updated


@app.patch("/attendance/{emp_code}/{date}")
def patch_attendance(emp_code: str, date: str, body: PatchAttendance):
    log = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not log:
        raise HTTPException(404, "Attendance record not found")
    
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    changes = {}
    set_fields = {}

    if body.status is not None and body.status != log.get("status"):
        changes["status"] = {"from": log.get("status"), "to": body.status}
        set_fields["status"] = body.status

    if body.punch_in is not None:
        new_pin = datetime.fromtimestamp(body.punch_in / 1000, tz=timezone.utc).replace(microsecond=0)
        old_pin = log.get("punch_in")
        if new_pin != old_pin:
            changes["punch_in"] = {
                "from": int(old_pin.timestamp() * 1000) if old_pin else None,
                "to": body.punch_in
            }
            set_fields["punch_in"] = new_pin
            
            local_pin = new_pin.astimezone(IST)
            new_late = compute_late_minutes(local_pin, emp["shift_start"])
            if new_late != log.get("late_minutes", 0):
                changes["late_minutes"] = {"from": log.get("late_minutes", 0), "to": new_late}
                set_fields["late_minutes"] = new_late

    if body.punch_out is not None:
        new_pout = datetime.fromtimestamp(body.punch_out / 1000, tz=timezone.utc).replace(microsecond=0)
        old_pout = log.get("punch_out")
        if new_pout != old_pout:
            changes["punch_out"] = {
                "from": int(old_pout.timestamp() * 1000) if old_pout else None,
                "to": body.punch_out
            }
            set_fields["punch_out"] = new_pout

    effective_pin = set_fields.get("punch_in", log.get("punch_in"))
    effective_pout = set_fields.get("punch_out", log.get("punch_out"))

    if effective_pin and effective_pout:
        new_wh = compute_work_hours(effective_pin, effective_pout)
        if new_wh != log.get("work_hours"):
            changes["work_hours"] = {"from": log.get("work_hours"), "to": new_wh}
            set_fields["work_hours"] = new_wh

        local_pout = effective_pout.astimezone(IST)
        new_ot = compute_overtime(local_pout, emp["shift_end"], date)
        if new_ot != log.get("overtime_minutes", 0):
            changes["overtime_minutes"] = {"from": log.get("overtime_minutes", 0), "to": new_ot}
            set_fields["overtime_minutes"] = new_ot

        new_hd = new_wh < 4.50
        if new_hd != log.get("half_day", False):
            changes["half_day"] = {"from": log.get("half_day", False), "to": new_hd}
            set_fields["half_day"] = new_hd

    if changes:
        history_entry = {
            "at": datetime.now(timezone.utc).replace(microsecond=0),
            "by": body.regularized_by,
            "reason": body.reason,
            "changes": changes
        }
        db.attendance_logs.update_one(
            {"_id": log["_id"]},
            {
                "$set": set_fields,
                "$push": {"history": history_entry}
            }
        )

    updated = db.attendance_logs.find_one({"_id": log["_id"]})
    updated["id"] = str(updated.pop("_id"))
    if updated.get("punch_in"):
        updated["punch_in"] = int(updated["punch_in"].timestamp() * 1000)
    if updated.get("punch_out"):
        updated["punch_out"] = int(updated["punch_out"].timestamp() * 1000)
    for h in updated.get("history", []):
        if "at" in h:
            h["at"] = int(h["at"].timestamp() * 1000)
        if "changes" in h:
            for field, val in h["changes"].items():
                if isinstance(val.get("from"), datetime):
                    val["from"] = int(val["from"].timestamp() * 1000)
                if isinstance(val.get("to"), datetime):
                    val["to"] = int(val["to"].timestamp() * 1000)

    return updated


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
        
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    docs = list(db.attendance_logs.find(q).sort("date", DESCENDING).skip(skip).limit(page_size))
    
    for d in docs:
        d["id"] = str(d.pop("_id"))
        if d.get("punch_in"):
            d["punch_in"] = int(d["punch_in"].timestamp() * 1000)
        if d.get("punch_out"):
            d["punch_out"] = int(d["punch_out"].timestamp() * 1000)
        for h in d.get("history", []):
            if "at" in h:
                h["at"] = int(h["at"].timestamp() * 1000)
            if "changes" in h:
                for field, val in h["changes"].items():
                    if isinstance(val.get("from"), datetime):
                        val["from"] = int(val["from"].timestamp() * 1000)
                    if isinstance(val.get("to"), datetime):
                        val["to"] = int(val["to"].timestamp() * 1000)
                        
    return {"items": docs, "total": total, "page": page, "page_size": page_size}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$regex": f"^ {month}"}} if False else {"emp_code": emp_code, "date": {"$regex": f"^{month}"}}},
        {
            "$group": {
                "_id": None,
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
                "total_work_hours": {"$sum": {"$ifNull": ["$work_hours", 0]}},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                            0
                        ]
                    }
                }
            }
        }
    ]
    res = list(db.attendance_logs.aggregate(pipeline))
    if not res:
        return {
            "emp_code": emp_code,
            "month": month,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0,
            "total_work_hours": 0.0,
            "present_days": 0.0
        }
    data = res[0]
    data.pop("_id", None)
    data["emp_code"] = emp_code
    data["month"] = month
    data["total_work_hours"] = round(data["total_work_hours"], 2)
    data["present_days"] = round(data["present_days"], 1)
    return data


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    pipeline = [
        {"$match": {"joined_on": {"$lte": f"{month}-31"}}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp"]},
                                    {"$regexMatch": {"input": "$date", "regex": f"^{month}"}}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$logs.overtime_minutes", 0]}},
                "total_work_hours": {"$sum": {"$ifNull": ["$logs.work_hours", 0]}},
                "total_present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1.0]},
                            0
                        ]
                    }
                },
                "log_count": {
                    "$sum": {
                        "$cond": [{"$ifNull": ["$logs.date", False]}, 1, 0]
                    }
                }
            }
        },
        {
            "$project": {
                "department": "$_id",
                "headcount": {"$size": "$headcount"},
                "total_late_minutes": 1,
                "total_overtime_minutes": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$log_count", 0]},
                        {"$round": [{"$divide": ["$total_work_hours", "$log_count"]}, 2]},
                        0.0
                    ]
                },
                "total_present_days": {"$round": ["$total_present_days", 1]},
                "_id": 0
            }
        },
        {"$sort": {"department": ASCENDING}}
    ]
    return {"items": list(db.employees.aggregate(pipeline))}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), limit: int = 10):
    pipeline = [
        {"$match": {"date": {"$regex": f"^{month}"}}},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp_info"
            }
        },
        {"$unwind": "$emp_info"},
        {
            "$project": {
                "emp_code": "$_id",
                "name": "$emp_info.name",
                "department": "$emp_info.department",
                "total_late_minutes": 1,
                "_id": 0
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": DESCENDING, "emp_code": ASCENDING},
                "output": {
                    "rank": {"$denseRank": {}}
                }
            }
        },
        {"$sort": {"rank": ASCENDING, "emp_code": ASCENDING}},
        {"$limit": limit}
    ]
    return {"items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, date_from: str, date_to: str):
    start_dt = datetime.fromisoformat(date_from)
    end_dt = datetime.fromisoformat(date_to)
    days_list = []
    curr = start_dt
    while curr <= end_dt:
        days_list.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)

    pipeline = [
        {"$match": {"department": department, "joined_on": {"$lte": date_to}}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$emp"]}, {"$gte": ["$date", date_from]}, {"$lte": ["$date", date_to]}]}}}
                ],
                "as": "logs"
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {"date": "$logs.date", "emp": "$emp_code"},
                "present": {
                    "$max": {
                        "$cond": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1.0]},
                            0
                        ]
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$_id.date",
                "present_count": {"$sum": "$present"}
            }
        }
    ]
    db_results = {item["_id"]: item["present_count"] for item in db.attendance_logs.database.employees.aggregate(pipeline)}
    
    items = []
    past_values = []
    for d in days_list:
        val = db_results.get(d, 0.0)
        past_values.append(val)
        if len(past_values) > 7:
            past_values.pop(0)
        ma = round(sum(past_values) / len(past_values), 2)
        items.append({"date": d, "present_count": round(val, 1), "moving_avg_7d": ma})

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def admin_explain(endpoint: str, **kwargs):
    if endpoint == "attendance_list":
        cursor = db.attendance_logs.find().sort("date", DESCENDING).limit(20)
    elif endpoint == "employee_monthly":
        cursor = db.attendance_logs.aggregate([{"$match": {"emp_code": "EMP0001"}}, {"$group": {"_id": "$emp_code"}}])
    elif endpoint == "department_summary":
        cursor = db.employees.aggregate([{"$match": {"department": "Engineering"}}, {"$lookup": {"from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code", "as": "l"}}])
    elif endpoint == "late_leaderboard":
        cursor = db.attendance_logs.aggregate([{"$match": {"date": {"$regex": "^2026-07"}}}, {"$group": {"_id": "$emp_code"}}])
    elif endpoint == "department_trend":
        cursor = db.employees.aggregate([{"$match": {"department": "Engineering"}}, {"$lookup": {"from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code", "as": "l"}}])
    else:
        raise HTTPException(404, "Invalid explain endpoint")
    
    return cursor.explain().get("executionStats", {})