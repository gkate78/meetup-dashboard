# Security Guidelines

## Secrets Management

### Local Development
1. **Never** commit secrets to the repository
2. Use environment variables set in your shell:
   ```bash
   export MEETUP_TOKEN="your-token-here"
   export SNAPSHOT_S3_BUCKET="your-bucket"
   export AWS_ACCESS_KEY_ID="your-key"
   export AWS_SECRET_ACCESS_KEY="your-secret"
   ```

3. Or use a local `.env` file (already in `.gitignore`):
   ```
   MEETUP_TOKEN=your-token-here
   SNAPSHOT_S3_BUCKET=your-bucket
   ```
   The app loads `.env` automatically in local development. If you need to load it into your shell, prefer a dotenv-aware tool instead of pasting secrets into terminal history.

### Streamlit Cloud Deployment
1. Go to app settings → Secrets
2. Add each secret as a separate key-value pair:
   - `MEETUP_TOKEN`
   - `SNAPSHOT_S3_BUCKET` (if using S3)
   - Any AWS credentials (if using S3)
   - `ADMIN_PASSWORD` (if enabling moderator access)

Do NOT paste a `.env` file or `secrets.toml` content directly.

## Preventing Secret Leaks

### What's Protected
The `.gitignore` file prevents these from being committed:
- `.streamlit/secrets.toml` - Local Streamlit secrets
- `.env*` - Environment variable files
- `aws_credentials.json` - AWS credential files
- `.aws/` - AWS CLI config directory
- Runtime SQLite databases under `data/`, known runtime CSV files under `data/`, and cache files under `cache/`

### Pre-commit Hook (Recommended)
Install a pre-commit hook to catch secrets before they're committed:

```bash
pip install pre-commit detect-secrets
pre-commit install
```

### Scanning Existing History
If you are preparing this repository for wider sharing, scan the current tree and history before changing visibility. Treat any finding as sensitive until the secret has been rotated.

## Rotating the MEETUP_TOKEN

Follow these steps to rotate your Meetup API token (do this periodically or if token may be exposed):

### 1. Generate a New Token
- Log in to [Meetup.com](https://www.meetup.com)
- Go to **Account** → **API** (or [Account Settings](https://www.meetup.com/account/))
- Find **OAuth Consumer** or **API Key** section
- Generate a new personal API token
- Copy the new token

### 2. Update Local Environment
```bash
# Option A: Update .env file (single quotes prevent shell expansion)
echo "MEETUP_TOKEN='your-new-token-here'" > .env

# Option B: Export to shell
export MEETUP_TOKEN='your-new-token-here'

# Test the app works
streamlit run meetup.py
```

### 3. Update Streamlit Cloud
- Go to your Streamlit app dashboard
- Click **Edit secrets**
- Update the `MEETUP_TOKEN` value with your new token
- Click **Save**
- Streamlit will redeploy automatically

### 4. Verify Everything Works
- Check your app logs for "Data source: Live API"
- Verify events load correctly
- Check Meetup API quota hasn't been exceeded

### 5. (Optional) Revoke Old Token
- Log back into Meetup.com → Account → API
- Revoke or delete the old token so it can't be used elsewhere

## If a Secret Was Leaked
If you accidentally commit a secret:

1. **Rotate immediately** - get a new token/key/password
2. **Remove from history** if needed. Prefer `git filter-repo` or the official GitHub sensitive-data removal process:
   ```bash
   git filter-repo --path <file> --invert-paths
   ```
3. Coordinate any force-push carefully, especially if other people may have cloned the repository.

## Code Review Checklist
Before committing:
- [ ] No hardcoded API keys, tokens, or passwords
- [ ] No `.env` files
- [ ] No `secrets.toml` files
- [ ] No AWS credentials in config files
- [ ] Secrets always loaded from environment variables or Streamlit secrets

## References
- [OWASP: Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [GitHub: Removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [Streamlit: Secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
