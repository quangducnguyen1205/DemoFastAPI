from fastapi import FastAPI
from .routers import users
from .database import engine, Base
import time

# Create database tables
def create_tables():
    # Wait for database to be ready
    max_retries = 30
    for i in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            print("Database tables created successfully")
            break
        except Exception as e:
            print(f"Attempt {i+1}/{max_retries} failed: {e}")
            if i < max_retries - 1:
                time.sleep(2)
            else:
                raise

# Create FastAPI app
app = FastAPI(
    title="User Management API",
    description="A FastAPI application for managing users with PostgreSQL",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Create database tables on startup
@app.on_event("startup")
def startup_event():
    create_tables()

# Include routers
app.include_router(users.router, prefix="/users", tags=["users"])

# Root endpoint
@app.get("/")
def read_root():
    return {
        "message": "Welcome to User Management API",
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}
