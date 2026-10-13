# Employee Attendance & Analytics API

A robust backend service built with FastAPI and MongoDB designed to track employee attendance, manage work shifts, maintain immutable audit trails, and generate advanced departmental analytics.

## Tech Stack
- **Python 3.11+**
- **FastAPI**
- **MongoDB 6.0+**
- **Pydantic v2**

## Core Features
- **Attendance Lifecycle:** Automated punch-in and punch-out handling, grace periods, shift timing rules (including overnight shift support), late-minute calculations, and half-day detection.
- **Audit Trails:** Manual attendance regularization (`PATCH`) that records previous vs. new field values, author tracking, and modification reasons.
- **Advanced Analytics:** Monthly employee summaries, departmental headcount/work-hour aggregations, late-comer leaderboards with ranking ties, and 7-day moving average department trends.

## Local Setup & Installation

1. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

```

2. **Install dependencies:**
```bash
pip install -r requirements.txt

```


3. **Configure environment variables:**
Create a `.env` file in the root folder with your MongoDB connection details:
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db

```


4. **Run the application:**
```bash
uvicorn app.main:app --port 8000 --reload

```


5. **Explore API Documentation:**
Open your browser and navigate to `http://localhost:8000/docs` to test all endpoints interactively.

