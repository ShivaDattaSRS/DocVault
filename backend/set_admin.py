"""Set admin flag for a user by email"""
import sys
from sqlalchemy import select, update
from app.database import SessionLocal, get_db
from app.models import User
from app.config import settings

def set_admin(email: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email.lower().strip()))
        if not user:
            print(f"❌ User '{email}' not found in database")
            return
        
        if user.is_admin:
            print(f"✓ User '{email}' is already admin")
            return
        
        user.is_admin = True
        db.commit()
        db.refresh(user)
        print(f"✓ User '{email}' has been set as admin")
        print(f"  - ID: {user.id}")
        print(f"  - Name: {user.full_name}")
        print(f"  - Verified: {user.is_verified}")
        print(f"  - Active: {user.is_active}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    email = "dattu5989@gmail.com"
    print(f"Setting admin access for: {email}")
    set_admin(email)
