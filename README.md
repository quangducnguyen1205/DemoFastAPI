# User Management API

A FastAPI application for managing users with PostgreSQL database and full CRUD operations.

## Features

- **FastAPI**: Modern, fast web framework for building APIs
- **PostgreSQL**: Robust relational database
- **SQLAlchemy**: Python SQL toolkit and ORM
- **Pydantic**: Data validation using Python type annotations
- **Docker**: Containerized application with Docker Compose
- **Full CRUD Operations**: Create, Read, Update, Delete users

## Project Structure

```
DemoFirstBackend/
├── backend/                 # FastAPI backend application
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py         # FastAPI app entry point
│   │   ├── database.py     # Database configuration
│   │   ├── models.py       # SQLAlchemy models
│   │   ├── schemas.py      # Pydantic schemas
│   │   └── routers/
│   │       ├── __init__.py
│   │       └── users.py    # User CRUD routes
│   ├── requirements.txt    # Python dependencies
│   └── Dockerfile         # Backend Docker configuration
├── docker compose.yml    # Docker Compose configuration
├── .env.example           # Environment variables example
├── .gitignore           # Git ignore rules
└── README.md           # This file
```

## Quick Start

### Using Docker Compose (Recommended)

1. Clone the repository
2. Navigate to the project directory
3. Run the application:

```bash
docker compose up --build
```

The API will be available at:
- **API Documentation**: http://localhost:8000/docs
- **Alternative Documentation**: http://localhost:8000/redoc
- **API Root**: http://localhost:8000/

### Local Development

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Start PostgreSQL database (using Docker):
```bash
docker run --name postgres-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=userdb -p 5432:5432 -d postgres:15
```

4. Run the application:
```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

### Users

- **GET** `/users/` - Get all users (with pagination)
- **POST** `/users/` - Create a new user
- **GET** `/users/{user_id}` - Get a specific user by ID
- **PUT** `/users/{user_id}` - Update a user
- **DELETE** `/users/{user_id}` - Delete a user

### Other

- **GET** `/` - Root endpoint
- **GET** `/health` - Health check endpoint

## API Usage Examples

### Create a User

```bash
curl -X POST "http://localhost:8000/users/" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "John Doe",
       "email": "john.doe@example.com"
     }'
```

### Get All Users

```bash
curl -X GET "http://localhost:8000/users/"
```

### Get a Specific User

```bash
curl -X GET "http://localhost:8000/users/1"
```

### Update a User

```bash
curl -X PUT "http://localhost:8000/users/1" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "John Smith",
       "email": "john.smith@example.com"
     }'
```

### Delete a User

```bash
curl -X DELETE "http://localhost:8000/users/1"
```

## Database Schema

### Users Table

| Column     | Type      | Constraints           |
|------------|-----------|----------------------|
| id         | Integer   | Primary Key, Auto-increment |
| name       | String(100) | Not Null           |
| email      | String(100) | Unique, Not Null, Indexed |
| created_at | DateTime  | Auto-generated       |
| updated_at | DateTime  | Auto-updated         |

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `POSTGRES_USER`: Database username
- `POSTGRES_PASSWORD`: Database password
- `POSTGRES_DB`: Database name

## Development

### Database Migrations

This project uses SQLAlchemy's `create_all()` method for simplicity. For production, consider using Alembic for database migrations.

## Production Deployment

1. Update environment variables for production
2. Use a production-grade PostgreSQL instance
3. Consider using a reverse proxy (nginx)
4. Set up proper logging and monitoring
5. Use Docker secrets for sensitive data

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

# FastAPI User Management API

This is a simple backend system built with **FastAPI**, providing full **CRUD** functionality for user management. It is fully containerized using **Docker**.

## 🚀 Features

- Create / Read / Update / Delete users
- SQLAlchemy ORM
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
