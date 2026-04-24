# Task: Add Profile Photo Upload

Allow users to upload a profile photo. The photo should be resized to 200x200px and served via a public URL on the user's profile page.

## Technical Context

- Flask web application with Jinja2 templates
- PostgreSQL database with SQLAlchemy ORM
- Static files served via nginx reverse proxy
- User model has `avatar_url` field (currently nullable, defaults to gravatar)
- File storage: local filesystem at `/uploads/avatars/` (nginx serves `/uploads/` directly)
- No existing file upload handling in the codebase
- Pillow is listed in requirements.txt but not currently imported anywhere

## Requirements

1. Add `POST /settings/avatar` endpoint accepting multipart form upload
2. Resize uploaded image to 200x200px square (center crop)
3. Save to `/uploads/avatars/{user_id}.jpg`
4. Update `user.avatar_url` to point to the new file
5. Show the avatar on the profile page template
6. Add a "remove avatar" option that resets to gravatar

## Scope

- JPEG and PNG uploads only
- Maximum file size: 5MB
- No CDN or cloud storage — local filesystem only
