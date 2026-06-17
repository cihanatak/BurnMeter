"""Burnmeter Pro — public Firebase web config.

These are NOT secrets: a Firebase web apiKey identifies the project, it does not grant
access. Security is enforced by Firestore Security Rules + Firebase Auth (verified email,
per-uid ownership) — exactly as in the Ages Tycoon app, whose config is likewise public.
Safe to commit. Pro entitlement is gated server-side (the `plan` field, writable only by
billing), so shipping this in the open-source client does not bypass Pro.
"""

FIREBASE = {
    "apiKey": "AIzaSyBnGqYJED4p4HIVWvd8Sr-_MvIpyud5K_0",
    "authDomain": "burnmeter-4e5f3.firebaseapp.com",
    "projectId": "burnmeter-4e5f3",
    "storageBucket": "burnmeter-4e5f3.firebasestorage.app",
    "messagingSenderId": "797959077232",
    "appId": "1:797959077232:web:4f70242640120791c8e3b5",
}
