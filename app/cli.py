"""
Administrative command line for ELIES.

Create the first administrator (or promote an existing user) without editing
MongoDB by hand:

    docker compose exec api python -m app.cli create-admin --username admin --email admin@example.org
    docker compose exec api python -m app.cli promote --username alice

When --password is omitted, ``create-admin`` prompts for one, or generates one
with --generate-password (printed once; the user must change it at first login).
"""
import argparse
import getpass
import sys
from datetime import datetime, timezone

from pydantic import ValidationError as PydanticValidationError
from pymongo.errors import DuplicateKeyError

from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import get_users_collection
from app.schemas import UserRegister
from app.utils.security import email_query, generate_secure_password, hash_password, revoke_user_tokens


DUPLICATE_USER = "A user with this username or email already exists (use 'promote' instead)"


def create_admin(username: str, email: str, password: str, full_name: str | None = None,
                 must_change_password: bool = False) -> dict:
    """Create an active administrator account. Raises ValueError on invalid input or duplicates."""
    try:
        data = UserRegister(username=username, email=email, password=password, full_name=full_name)
    except PydanticValidationError as exc:
        raise ValueError("; ".join(err["msg"] for err in exc.errors())) from exc

    users = get_users_collection()
    email = data.email.lower()
    if users.find_one({"$or": [{"username": data.username}, {"email": email_query(email)}]}):
        raise ValueError(DUPLICATE_USER)

    now = datetime.now(timezone.utc)
    user_doc = {
        "username": data.username,
        "email": email,
        "hashed_password": hash_password(data.password),
        "full_name": data.full_name,
        "is_active": True,
        "roles": ["user", "admin"],
        "token_version": 0,
        "must_change_password": must_change_password,
        "storage_used_bytes": 0,
        "storage_limit_bytes": DEFAULT_USER_STORAGE_QUOTA,
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
    }
    try:
        user_doc["_id"] = users.insert_one(user_doc).inserted_id
    except DuplicateKeyError as exc:  # created concurrently
        raise ValueError(DUPLICATE_USER) from exc
    return user_doc


def promote(username: str) -> dict:
    """Give an existing user the admin role. Raises ValueError if the user does not exist."""
    users = get_users_collection()
    user = users.find_one({"username": username})
    if not user:
        raise ValueError(f"User '{username}' not found")
    roles = sorted(set(user.get("roles", ["user"])) | {"admin", "user"})
    users.update_one({"_id": user["_id"]}, {"$set": {"roles": roles, "updated_at": datetime.now(timezone.utc)}})
    revoke_user_tokens(user["_id"])
    return users.find_one({"_id": user["_id"]})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="ELIES administration")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="Create an administrator account")
    create.add_argument("--username", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--full-name")
    group = create.add_mutually_exclusive_group()
    group.add_argument("--password", help="Avoid on shared machines: it ends up in shell history")
    group.add_argument("--generate-password", action="store_true",
                       help="Generate a random password and require a change at first login")

    prom = sub.add_parser("promote", help="Give an existing user the admin role")
    prom.add_argument("--username", required=True)

    sub.add_parser("reconcile-storage",
                   help="Recompute every user's storage usage from disk (also run daily by Celery beat)")

    args = parser.parse_args(argv)
    try:
        if args.command == "create-admin":
            generated = False
            password = args.password
            if args.generate_password:
                password, generated = generate_secure_password(20), True
            elif not password:
                password = getpass.getpass("Password: ")
                if password != getpass.getpass("Repeat password: "):
                    print("Passwords do not match", file=sys.stderr)
                    return 1
            user = create_admin(args.username, args.email, password, args.full_name, must_change_password=generated)
            print(f"Created administrator '{user['username']}' ({user['_id']})")
            if generated:
                print(f"Generated password (shown once): {password}")
        elif args.command == "reconcile-storage":
            from app.services.storage_service import reconcile_all_storage

            corrected = reconcile_all_storage()
            print(f"Storage usage corrected for {len(corrected)} users")
        else:
            user = promote(args.username)
            print(f"'{user['username']}' now has roles: {', '.join(user['roles'])}")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
