# BahriaAI Policy App

Same Bahria University Policy Bot as the website: guest chat, streaming answers, sources, login, and staff console.

## Check it (web)

```powershell
cd "D:\behria chatbot\mobile"
npm run web
```

Open **http://localhost:8081/**

## Android APK

Install `D:\behria chatbot\BahriaAI.apk` on your phone (allow unknown apps).

The APK is for **same-WiFi testing** against this laptop (not the campus VM). Phone and laptop must be on the same Wi-Fi, and Django must be listening on `0.0.0.0:8000`.

Install the new `D:\behria chatbot\BahriaAI.apk`, then keep this running on the laptop.

Rebuild:

```powershell
cd "D:\behria chatbot\mobile"
powershell -File .\build-apk.ps1
```
