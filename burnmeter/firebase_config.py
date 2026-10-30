"""Burnmeter Pro — public Firebase web config.

These are NOT secrets: a Firebase web apiKey identifies the project, it does not grant
access. What protects the data is Firebase Auth plus firestore.rules (repo root): an
account reaches only its own documents, and only after verifying its email. Safe to commit.

Pro is enforced by the deployed copy of those rules alone: device sync needs
`plan == "pro"` on /users/{uid}, a document no client can write (billing, or
scripts/pro_admin.py grant, sets it). The client is open source, so nothing it checks
can enforce Pro. Deploying and rolling back the rules: docs/FIRESTORE_RULES.md.
"""

FIREBASE = {
    "apiKey": "AIzaSyBnGqYJED4p4HIVWvd8Sr-_MvIpyud5K_0",
    "authDomain": "burnmeter-4e5f3.firebaseapp.com",
    "projectId": "burnmeter-4e5f3",
    "storageBucket": "burnmeter-4e5f3.firebasestorage.app",
    "messagingSenderId": "797959077232",
    "appId": "1:797959077232:web:4f70242640120791c8e3b5",
}
