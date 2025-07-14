# FastAPI User Management API

This is a simple backend system built with **FastAPI**, providing full **CRUD** functionality for user management. It uses **SQLite** as the database and is fully containerized using **Docker**.

## 🚀 Features

- Create / Read / Update / Delete users
- SQLite + SQLAlchemy ORM
- Pydantic-based validation
- Swagger UI for interactive testing
- Dockerized with Gunicorn + Uvicorn worker

## 📦 Requirements

- Python 3.9+
- Docker

## 🛠️ How to Run
### 1. Clone the repo
```bash
git clone https://github.com/quangducnguyen1205/DemoFastAPI
cd DemoFastAPI
```
### 2. Build the Docker image
```bash
docker build -t fastapi-demo .
```
### 3. Run the container
```bash
docker run -p 8000:8000 fastapi-demo
```
### 4. Access the API
Visit: http://localhost:8000/docs

## 📂 Project Structure
```bash
.
├── main.py
├── models.py
├── schemas.py
├── database.py
├── requirements.txt
└── Dockerfile
```

## 📬 Author

Nguyễn Quang Đức – duc010205@gmail.com  
Student at Hanoi University of Science and Technology
