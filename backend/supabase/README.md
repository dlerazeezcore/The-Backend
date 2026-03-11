# Supabase Backend Layer

This package contains Supabase access for backend modules.

- `supabase.py`: shared client utilities (`is_enabled`, `load_or_seed`, `save`)
- `auth/users_repo.py`: users document access
- `communications/email_config_repo.py`: email config document access
- `passenger_database/passenger_db_repo.py`: passenger profiles/history document access
- `pending/pending_repo.py`: pending document access
- `transactions/transactions_repo.py`: transactions document access

Keep API/business logic in domain folders (`backend/auth`, `backend/communications`, etc.) and keep raw Supabase read/write logic in this folder.
