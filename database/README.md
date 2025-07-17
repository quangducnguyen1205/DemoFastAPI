# Database Configuration

This directory contains database-related configuration and documentation.

## PostgreSQL Database

The application uses PostgreSQL as its primary database, configured via Docker Compose.

### Database Configuration:
- **Image**: postgres:15
- **Database Name**: userdb
- **Username**: postgres
- **Password**: postgres
- **Port**: 5432

### Environment Variables:
- `POSTGRES_USER`: Database username
- `POSTGRES_PASSWORD`: Database password
- `POSTGRES_DB`: Database name

### Volume Mapping:
- `postgres_data`: Persistent volume for database data

### Connection String:
For local development: `postgresql://postgres:postgres@localhost:5432/userdb`
For Docker containers: `postgresql://postgres:postgres@db:5432/userdb`

## Database Schema

The application uses SQLAlchemy ORM with the following models:

### Users Table
- `id`: Primary key (Integer)
- `name`: User's name (String, max 100 chars)
- `email`: User's email (String, max 100 chars, unique)
- `created_at`: Timestamp of creation
- `updated_at`: Timestamp of last update

## Database Migrations

Currently using SQLAlchemy's `create_all()` method. For production, consider implementing Alembic for database migrations.
