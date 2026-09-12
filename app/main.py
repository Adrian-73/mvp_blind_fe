import os
import sys
import json
import time
from uuid import UUID
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from dotenv import load_dotenv

# Load local environment variables from .env file
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


from app.database import init_db, close_db, get_db_connection
from app.auth import get_current_user, get_current_admin, get_user_session, verify_password
from app.sessions import (
    USER_SESSION, ADMIN_SESSION, TrustedOriginMiddleware, allowed_origins, is_trusted_origin,
    start_session, find_session, end_session, end_all_user_sessions, clear_session_cookie
)
from app.schemas import (
    SignupRequest, LoginRequest, AuthResponse,
    SendOtpRequest, VerifyOtpRequest,
    StatusResponse, MessagesListResponse, MessageResponse,
    AdminLoginRequest, AdminSessionResponse, AdminUsersListResponse,
    MatchRequest, MatchResponse, AdminRoomsListResponse, AdminEmailConfigResponse,
    age_from_date_of_birth
)
from app.services import (
    register_user, authenticate_user, get_user_status, submit_quiz,
    get_room_messages, get_admin_users, match_users, get_admin_rooms, deactivate_room, delete_user,
    send_email_otp, check_email_otp, delete_email_otp, verify_email_otp
)
from app.state import connections
from app.mailer import is_email_configured
from supabase import Client

# Lifespan manager for DB connection pool
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

tags_metadata = [
    {
        "name": "System Health",
        "description": "Public, unauthenticated health checks and live heartbeat metrics.",
    },
    {
        "name": "User Authentication",
        "description": "Registration, login and logout. Logging in sets an httpOnly session cookie that every user endpoint reads.",
    },
    {
        "name": "User Operations & Chat",
        "description": "Polled status checks, historical room chats, quiz submissions, and active client rooms.",
    },
    {
        "name": "Admin Authentication",
        "description": "Admin login and logout, backed by a short-lived httpOnly session cookie.",
    },
    {
        "name": "Admin Control Panel",
        "description": "Matchmaking controllers, user account search lists, and active room deactivation panels.",
    },
    {
        "name": "Admin Data Exports",
        "description": "Streamed high-volume CSV downloads for user tables and historical chat transcripts.",
    }
]

app = FastAPI(
    title="Private Blind-Dating Chat Room API",
    description="Backend API for managing private anonymous chat rooms, matchmaking, and real-time WebSockets.",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    docs_url=None,
    redoc_url=None
)

# Blocks cross-site POSTs riding on the session cookies. Added before CORS so CORS stays outermost.
app.add_middleware(TrustedOriginMiddleware)

# CORS Configuration: credentials on, so the session cookie also works from the Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Dynamic Swagger Console Custom HTML Template ---

SWAGGER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Private Blind-Dating Chat Room API - Interactive Console</title>
    <link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
    <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&family=Fira+Code:wght@400;500&display=swap');

        body {
            background-color: #0b0f19 !important;
            background-image: radial-gradient(circle at 50% 0px, #1e1b4b 0%, #0b0f19 800px) !important;
            margin: 0;
            font-family: 'Inter', sans-serif;
            color: #cbd5e1;
        }

        .premium-header {
            max-width: 1460px;
            margin: 0 auto;
            padding: 30px 20px 10px 20px;
        }

        .metrics-row {
            display: grid;
            grid-template-columns: 1.5fr 1fr 1fr 1fr 1fr;
            gap: 20px;
            margin-bottom: 25px;
        }

        @media (max-width: 1024px) {
            .metrics-row {
                grid-template-columns: 1fr 1fr;
            }
            .metrics-row > :first-child {
                grid-column: span 2;
            }
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 15px;
            background: rgba(30, 41, 59, 0.35);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 20px;
        }

        .glowing-heart {
            width: 24px;
            height: 24px;
            background-color: #ec4899;
            position: relative;
            transform: rotate(-45deg);
            animation: heartbeat 1.2s infinite;
            box-shadow: 0 0 15px rgba(236, 72, 153, 0.6);
        }

        .glowing-heart::before, .glowing-heart::after {
            content: "";
            width: 24px;
            height: 24px;
            background-color: #ec4899;
            border-radius: 50%;
            position: absolute;
        }

        .glowing-heart::before {
            top: -12px;
            left: 0;
        }

        .glowing-heart::after {
            top: 0;
            left: 12px;
        }

        @keyframes heartbeat {
            0% { transform: rotate(-45deg) scale(1); }
            25% { transform: rotate(-45deg) scale(1.1); }
            35% { transform: rotate(-45deg) scale(1.05); }
            45% { transform: rotate(-45deg) scale(1.15); }
            100% { transform: rotate(-45deg) scale(1); }
        }

        .brand-text h2 {
            margin: 0;
            font-family: 'Outfit', sans-serif;
            font-size: 20px;
            font-weight: 700;
            color: #f3f4f6;
            letter-spacing: -0.5px;
        }

        .brand-text span {
            font-size: 12px;
            color: #a78bfa;
            font-weight: 500;
        }

        .metric-card {
            background: rgba(30, 41, 59, 0.35);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 16px;
            padding: 16px 20px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.15);
        }

        .metric-label {
            font-size: 11px;
            font-weight: 600;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 8px;
        }

        .metric-value-container {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .metric-value {
            font-size: 22px;
            font-family: 'Outfit', sans-serif;
            font-weight: 700;
            color: #f1f5f9;
        }

        .text-purple { color: #c084fc !important; text-shadow: 0 0 10px rgba(192, 132, 252, 0.3); }
        .text-teal { color: #2dd4bf !important; text-shadow: 0 0 10px rgba(45, 212, 191, 0.3); }
        .text-green { color: #34d399 !important; text-shadow: 0 0 10px rgba(52, 211, 153, 0.3); }

        .metric-unit {
            font-size: 12px;
            color: #64748b;
            margin-left: 2px;
            font-weight: 500;
        }

        .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }

        .dot.green { background-color: #10b981; box-shadow: 0 0 10px #10b981; }
        .dot.amber { background-color: #f59e0b; box-shadow: 0 0 10px #f59e0b; }
        .dot.red { background-color: #ef4444; box-shadow: 0 0 10px #ef4444; }

        .dot.heartbeat {
            animation: glow-pulse 1.8s infinite;
        }

        @keyframes glow-pulse {
            0% { transform: scale(1); box-shadow: 0 0 4px #10b981; }
            50% { transform: scale(1.2); box-shadow: 0 0 12px #10b981; }
            100% { transform: scale(1); box-shadow: 0 0 4px #10b981; }
        }

        .sandbox-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 10px;
        }

        @media (max-width: 768px) {
            .sandbox-row {
                grid-template-columns: 1fr;
            }
        }

        .sandbox-card {
            backdrop-filter: blur(12px);
            border-radius: 16px;
            padding: 20px 24px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .sandbox-card.card-purple {
            background: linear-gradient(135deg, rgba(88, 28, 135, 0.15) 0%, rgba(30, 41, 59, 0.4) 100%);
            border: 1px solid rgba(139, 92, 246, 0.15);
        }

        .sandbox-card.card-teal {
            background: linear-gradient(135deg, rgba(17, 94, 89, 0.15) 0%, rgba(30, 41, 59, 0.4) 100%);
            border: 1px solid rgba(20, 184, 166, 0.15);
        }

        .sandbox-card h3 {
            margin: 0 0 8px 0;
            font-family: 'Outfit', sans-serif;
            font-size: 16px;
            font-weight: 600;
        }

        .sandbox-card p {
            font-size: 12px;
            color: #94a3b8;
            line-height: 1.5;
            margin: 0 0 16px 0;
        }

        .sandbox-inputs {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 16px;
        }

        .sandbox-inputs input {
            background: rgba(15, 23, 42, 0.6) !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            border-radius: 8px !important;
            padding: 10px 14px !important;
            color: #f1f5f9 !important;
            font-size: 12px !important;
            font-family: 'Inter', sans-serif !important;
            outline: none !important;
            transition: all 0.3s ease !important;
        }

        .sandbox-inputs input:focus {
            border-color: rgba(20, 184, 166, 0.4) !important;
            box-shadow: 0 0 10px rgba(20, 184, 166, 0.1) !important;
        }

        .sandbox-actions {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .sandbox-btn {
            border: none;
            border-radius: 8px;
            padding: 10px 18px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .btn-purple {
            background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%);
            color: white;
            box-shadow: 0 4px 15px rgba(139, 92, 246, 0.25);
        }

        .btn-purple:hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px rgba(139, 92, 246, 0.4);
        }

        .btn-teal {
            background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%);
            color: white;
            box-shadow: 0 4px 15px rgba(13, 148, 136, 0.25);
        }

        .btn-teal:hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px rgba(13, 148, 136, 0.4);
        }

        .status-msg {
            font-size: 11px;
            font-weight: 500;
            transition: opacity 0.3s ease;
        }

        .status-msg.success { color: #34d399; }
        .status-msg.error { color: #f87171; }

        .btn-spinner {
            width: 12px;
            height: 12px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 0.8s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* Swagger UI Dark Theme Override */
        .swagger-ui {
            background-color: transparent !important;
            color: #cbd5e1 !important;
        }
        .swagger-ui .info .title {
            color: #f3f4f6 !important;
            font-family: 'Outfit', sans-serif !important;
            font-weight: 700 !important;
        }
        .swagger-ui .info p, .swagger-ui .info li, .swagger-ui .info td {
            color: #9ca3af !important;
        }
        .swagger-ui .scheme-container {
            background: rgba(30, 41, 59, 0.4) !important;
            backdrop-filter: blur(12px) !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            border-radius: 16px !important;
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.15) !important;
            margin: 20px 0 !important;
            padding: 20px !important;
        }
        .swagger-ui select, .swagger-ui input[type=text] {
            background-color: #1f2937 !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            color: #f3f4f6 !important;
            border-radius: 6px !important;
            padding: 8px 12px !important;
        }
        .swagger-ui .opblock {
            background: rgba(17, 24, 39, 0.6) !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1) !important;
        }
        .swagger-ui .opblock.opblock-get {
            border-color: rgba(16, 185, 129, 0.3) !important;
            background: rgba(16, 185, 129, 0.05) !important;
        }
        .swagger-ui .opblock.opblock-post {
            border-color: rgba(59, 130, 246, 0.3) !important;
            background: rgba(59, 130, 246, 0.05) !important;
        }
        .swagger-ui .opblock.opblock-put {
            border-color: rgba(245, 158, 11, 0.3) !important;
            background: rgba(245, 158, 11, 0.05) !important;
        }
        .swagger-ui .opblock.opblock-delete {
            border-color: rgba(239, 68, 68, 0.3) !important;
            background: rgba(239, 68, 68, 0.05) !important;
        }
        .swagger-ui .opblock-summary-method {
            border-radius: 8px !important;
            font-weight: 700 !important;
            font-family: 'Outfit', sans-serif !important;
        }
        .swagger-ui .opblock-summary-path {
            color: #f3f4f6 !important;
            font-family: 'Fira Code', monospace !important;
            font-size: 14px !important;
        }
        .swagger-ui .opblock-summary-description {
            color: #9ca3af !important;
        }
        .swagger-ui .btn.authorize {
            background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%) !important;
            border: none !important;
            color: white !important;
            border-radius: 8px !important;
            box-shadow: 0 4px 14px rgba(139, 92, 246, 0.4) !important;
            font-weight: 600 !important;
            transition: all 0.3s ease !important;
            padding: 8px 20px !important;
        }
        .swagger-ui .btn.authorize:hover {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px rgba(139, 92, 246, 0.6) !important;
        }
        .swagger-ui .btn.authorize svg {
            fill: white !important;
        }
        .swagger-ui .authorization__btn svg {
            fill: #a78bfa !important;
        }
        .swagger-ui .opblock .opblock-summary-method-get { background: #10b981 !important; color: white !important; }
        .swagger-ui .opblock .opblock-summary-method-post { background: #3b82f6 !important; color: white !important; }
        .swagger-ui .opblock .opblock-summary-method-put { background: #f59e0b !important; color: white !important; }
        .swagger-ui .opblock .opblock-summary-method-delete { background: #ef4444 !important; color: white !important; }
        .swagger-ui .dialog-ux .modal-ux {
            background-color: #111827 !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 16px !important;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5) !important;
        }
        .swagger-ui .dialog-ux .modal-ux-header h3 {
            color: #f3f4f6 !important;
            font-family: 'Outfit', sans-serif !important;
        }
        .swagger-ui .dialog-ux .modal-ux-content {
            color: #cbd5e1 !important;
        }
        .swagger-ui .dialog-ux .modal-ux-header .close-button {
            fill: #9ca3af !important;
        }
        .swagger-ui .model-box {
            background-color: #111827 !important;
            border: 1px solid rgba(255, 255, 255, 0.05) !important;
            border-radius: 8px !important;
            padding: 10px !important;
        }
        .swagger-ui .model {
            color: #cbd5e1 !important;
        }
        .swagger-ui .prop-type {
            color: #f472b6 !important;
        }
        .swagger-ui .prop-format {
            color: #9ca3af !important;
        }
        .swagger-ui table thead tr td, .swagger-ui table thead tr th {
            color: #cbd5e1 !important;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important;
        }
        .swagger-ui .parameter__name.required {
            color: #ef4444 !important;
        }
        .swagger-ui .parameter__name {
            color: #f3f4f6 !important;
        }
        .swagger-ui .response-col_status {
            color: #f3f4f6 !important;
        }
        .swagger-ui .topbar {
            display: none !important;
        }
        .swagger-ui .info {
            margin: 15px 0 20px 0 !important;
        }
    </style>
</head>
<body>
    <div class="premium-header">
        <div class="metrics-row">
            <div class="brand">
                <div class="glowing-heart"></div>
                <div class="brand-text">
                    <h2>Private Blind-Dating API</h2>
                    <span>Interactive Developer Workspace</span>
                </div>
            </div>
            
            <div class="metric-card">
                <span class="metric-label">Database Status</span>
                <div class="metric-value-container">
                    <div id="db-status-dot" class="dot amber"></div>
                    <span id="db-status-text" class="metric-value">Checking...</span>
                </div>
            </div>
            
            <div class="metric-card">
                <span class="metric-label">Active Matches</span>
                <div class="metric-value-container">
                    <span id="active-rooms-count" class="metric-value text-purple">--</span>
                    <span class="metric-unit">rooms</span>
                </div>
            </div>
            
            <div class="metric-card">
                <span class="metric-label">WebSocket Listeners</span>
                <div class="metric-value-container">
                    <span id="connected-users-count" class="metric-value text-teal">--</span>
                    <span class="metric-unit">connected</span>
                </div>
            </div>
            
            <div class="metric-card">
                <span class="metric-label">Uptime Check</span>
                <div class="metric-value-container">
                    <div class="dot green heartbeat"></div>
                    <span class="metric-value text-green">Online</span>
                </div>
            </div>
        </div>

        <div class="sandbox-row">
            <div class="sandbox-card card-purple">
                <div>
                    <h3>🎭 Quick User Sandbox</h3>
                    <p>Instantly register a new random user and log in as them. Their session cookie rides along with every "Try it out" call below.</p>
                </div>
                <div class="sandbox-actions">
                    <button id="btn-user-auth" class="sandbox-btn btn-purple">
                        <span class="btn-spinner" id="spinner-user" style="display: none;"></span>
                        Auto-Register & Auth User
                    </button>
                    <span id="user-status" class="status-msg"></span>
                </div>
            </div>
            
            <div class="sandbox-card card-teal">
                <div>
                    <h3>🔑 Quick Admin Sandbox</h3>
                    <p>Log in with admin credentials. The admin session cookie authorizes the admin endpoints below.</p>
                </div>
                <div>
                    <div class="sandbox-inputs">
                        <input type="text" id="admin-user-input" placeholder="Admin Username" value="test_admin" />
                        <input type="password" id="admin-pass-input" placeholder="Admin Password" value="test_password" />
                    </div>
                    <div class="sandbox-actions">
                        <button id="btn-admin-auth" class="sandbox-btn btn-teal">
                            <span class="btn-spinner" id="spinner-admin" style="display: none;"></span>
                            Authenticate Admin
                        </button>
                        <span id="admin-status" class="status-msg"></span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div id="swagger-ui"></div>

    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
    <script>
        // Initialize Swagger UI and assign to window for sandbox usage
        window.onload = function() {
            window.ui = SwaggerUIBundle({
                url: "/openapi.json",
                dom_id: "#swagger-ui",
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.SwaggerUIStandalonePreset
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "BaseLayout"
            });
            
            // Trigger initial metrics fetch once Swagger loads
            updateMetrics();
        };

        // Function to update metrics
        async function updateMetrics() {
            try {
                const response = await fetch('/api/public/metrics');
                if (response.ok) {
                    const data = await response.json();
                    
                    const dbDot = document.getElementById('db-status-dot');
                    const dbText = document.getElementById('db-status-text');
                    if (data.db_connected) {
                        dbDot.className = 'dot green';
                        dbText.innerText = 'Connected';
                        dbText.className = 'metric-value text-green';
                    } else {
                        dbDot.className = 'dot red';
                        dbText.innerText = 'Offline';
                        dbText.className = 'metric-value text-red';
                    }
                    
                    document.getElementById('active-rooms-count').innerText = data.active_rooms_count;
                    document.getElementById('connected-users-count').innerText = data.connected_users_count;
                }
            } catch (e) {
                console.error("Failed fetching metrics:", e);
            }
        }

        // Fetch metrics every 5 seconds
        setInterval(updateMetrics, 5000);

        // Auto-Register & Auth Mock User
        document.getElementById('btn-user-auth').addEventListener('click', async () => {
            const btn = document.getElementById('btn-user-auth');
            const spinner = document.getElementById('spinner-user');
            const statusMsg = document.getElementById('user-status');
            
            btn.disabled = true;
            spinner.style.display = 'inline-block';
            statusMsg.innerText = '';
            
            try {
                const randId = Math.floor(Math.random() * 90000) + 10000;
                const testEmail = `dev_user_${randId}@example.com`;
                const testPassword = `pass_${randId}_secure`;
                
                const signupRes = await fetch('/api/signup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: testEmail,
                        password: testPassword,
                        date_of_birth: "1998-04-12",
                        gender: "female",
                        interested_in: ["male"],
                        state: "Karnataka",
                        bio: "Auto-registered sandbox user for exercising the API.",
                        single_reason: "Just a test account, married to the API.",
                        quiz_answers: {
                            q1: "spontaneous",
                            q2: "introvert",
                            q3: "gaming",
                            q4: "night",
                            q5: "dogs"
                        }
                    })
                });
                
                if (!signupRes.ok) {
                    throw new Error(`Signup failed: ${signupRes.statusText}`);
                }
                
                // The response set the session cookie, which Swagger's same-origin requests send from here on
                statusMsg.className = "status-msg success";
                statusMsg.innerText = `Logged in: ${testEmail}`;
                
            } catch (err) {
                statusMsg.className = "status-msg error";
                statusMsg.innerText = err.message || "Failed auto-auth";
            } finally {
                btn.disabled = false;
                spinner.style.display = 'none';
                updateMetrics();
            }
        });

        // Authenticate Admin
        document.getElementById('btn-admin-auth').addEventListener('click', async () => {
            const btn = document.getElementById('btn-admin-auth');
            const spinner = document.getElementById('spinner-admin');
            const statusMsg = document.getElementById('admin-status');
            const username = document.getElementById('admin-user-input').value;
            const password = document.getElementById('admin-pass-input').value;
            
            btn.disabled = true;
            spinner.style.display = 'inline-block';
            statusMsg.innerText = '';
            
            try {
                const loginRes = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                
                if (!loginRes.ok) {
                    throw new Error("Invalid admin credentials");
                }
                
                // The response set the admin session cookie
                statusMsg.className = "status-msg success";
                statusMsg.innerText = "Admin logged in";
                
            } catch (err) {
                statusMsg.className = "status-msg error";
                statusMsg.innerText = err.message || "Login failed";
            } finally {
                btn.disabled = false;
                spinner.style.display = 'none';
                updateMetrics();
            }
        });
    </script>
</body>
</html>
"""

# --- Dynamic Setup Page Template ---

SETUP_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Database Setup Required</title>
    <link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&family=Fira+Code:wght@400;500&display=swap');
        body {
            background-color: #0b0f19;
            background-image: radial-gradient(circle at 50% 0px, #1e1b4b 0%, #0b0f19 800px);
            margin: 0; font-family: 'Inter', sans-serif; color: #cbd5e1;
            display: flex; justify-content: center; align-items: center; min-height: 100vh;
        }
        .container {
            background: rgba(30, 41, 59, 0.35); backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px;
            padding: 40px; max-width: 800px; width: 90%; box-shadow: 0 4px 30px rgba(0, 0, 0, 0.15);
        }
        h1 { font-family: 'Outfit', sans-serif; color: #f3f4f6; margin-top: 0; }
        p { line-height: 1.6; }
        .sql-container {
            background: #1e293b; border-radius: 8px; padding: 20px;
            overflow-x: auto; font-family: 'Fira Code', monospace;
            font-size: 13px; color: #34d399; position: relative;
            margin: 20px 0; border: 1px solid rgba(255,255,255,0.1);
        }
        .copy-btn {
            position: absolute; top: 10px; right: 10px;
            background: #4f46e5; color: white; border: none;
            padding: 6px 12px; border-radius: 6px; cursor: pointer;
            font-family: 'Inter', sans-serif; font-size: 12px; font-weight: 500;
        }
        .copy-btn:hover { background: #4338ca; }
        .refresh-btn {
            background: #ec4899; color: white; border: none;
            padding: 12px 24px; border-radius: 8px; cursor: pointer;
            font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 600;
            display: block; margin: 0 auto; margin-top: 30px;
        }
        .refresh-btn:hover { background: #be185d; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚠️ Database Setup Required</h1>
        <p>It looks like your Supabase database hasn't been initialized with the required tables yet. Since we use the secure Supabase HTTP API, we cannot execute Data Definition (DDL) queries directly.</p>
        <p><strong>Action Required:</strong> Please copy the SQL snippet below and run it inside your <a href="https://supabase.com/dashboard/project/_/sql" target="_blank" style="color: #c084fc;">Supabase Dashboard SQL Editor</a>.</p>
        
        <div class="sql-container">
            <button class="copy-btn" onclick="copySql(this)">Copy SQL</button>
            <pre id="sql-content">{{SCHEMA_SQL}}</pre>
        </div>

        <button class="refresh-btn" onclick="window.location.reload()">I've run the SQL - Refresh</button>
    </div>

    <script>
        function copySql(btn) {
            const sql = document.getElementById('sql-content').innerText;
            navigator.clipboard.writeText(sql).then(() => {
                const originalText = btn.innerText;
                btn.innerText = 'Copied!';
                btn.style.background = '#10b981';
                setTimeout(() => {
                    btn.innerText = originalText;
                    btn.style.background = '#4f46e5';
                }, 2000);
            });
        }
    </script>
</body>
</html>
"""

# --- System Metrics & Custom Swagger Console Routes ---

@app.get("/api/public/metrics", status_code=status.HTTP_200_OK, include_in_schema=False)
async def public_metrics(db: Client = Depends(get_db_connection)):
    """Exposes real-time system health and websocket usage counters (public/developer-facing)."""
    db_connected = False
    try:
        db.table("rooms").select("id").limit(1).execute()
        db_connected = True
    except Exception as e:
        print(f"Metrics DB connection check failed: {e}", file=sys.stderr)

    active_rooms = 0
    if db_connected:
        try:
            res = db.table("rooms").select("id", count="exact").eq("is_active", True).execute()
            active_rooms = res.count if res.count is not None else len(res.data)
        except Exception as e:
            print(f"Metrics rooms query failed: {e}", file=sys.stderr)

    connected_users = sum(len(users) for users in connections.values())

    return {
        "db_connected": db_connected,
        "active_rooms_count": active_rooms,
        "connected_users_count": connected_users
    }

@app.get("/docs", include_in_schema=False)
async def custom_swagger_docs(db: Client = Depends(get_db_connection)):
    """Renders the custom premium developer console or a Setup UI if the database is empty."""
    db_connected = False
    try:
        db.table("rooms").select("id").limit(1).execute()
        db_connected = True
    except Exception as e:
        print(f"Schema detection check failed (likely missing tables): {e}", file=sys.stderr)
        
    if not db_connected:
        try:
            with open("schema.sql", "r", encoding="utf-8") as f:
                schema_content = f.read()
        except FileNotFoundError:
            schema_content = "-- schema.sql file not found!"
            
        return HTMLResponse(
            content=SETUP_TEMPLATE.replace("{{SCHEMA_SQL}}", schema_content), 
            status_code=status.HTTP_200_OK
        )
        
    return HTMLResponse(content=SWAGGER_TEMPLATE, status_code=status.HTTP_200_OK)

@app.get("/health", status_code=status.HTTP_200_OK, tags=["System Health"])
async def health_check():
    """Unauthenticated health endpoint used by Render and cron-job.org."""
    return {"status": "ok"}

# --- User Auth Endpoints ---

@app.post("/api/signup", response_model=AuthResponse, status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    db: Client = Depends(get_db_connection)
):
    """Signs up a new user once the code emailed by /api/auth/send-otp checks out, logs them in with a session cookie and returns their profile."""
    await check_email_otp(body.email, body.otp_code, db)
    user = await register_user(body.email, body.password, body.quiz_answers, body.profile_fields(), db)
    # The account exists now, so the code must not work again
    delete_email_otp(body.email, db)
    start_session(db, USER_SESSION, request, response, user_id=user["id"])
    return {"user": user}

@app.post("/api/auth/login", response_model=AuthResponse, status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Client = Depends(get_db_connection)
):
    """Verifies user credentials, starts a fresh session cookie and returns the user profile."""
    user = await authenticate_user(body.email, body.password, db)
    start_session(db, USER_SESSION, request, response, user_id=user["id"])
    return {"user": user}

@app.post("/api/auth/send-otp", status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def send_otp(
    body: SendOtpRequest,
    db: Client = Depends(get_db_connection)
):
    """Emails a 6-digit code, valid for 10 minutes, that /api/signup requires. Without SMTP configured it is only printed to the server log."""
    return await send_email_otp(body.email, db)

@app.post("/api/auth/verify-otp", response_model=AuthResponse, status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def verify_otp(
    body: VerifyOtpRequest,
    request: Request,
    response: Response,
    db: Client = Depends(get_db_connection)
):
    """Logs an existing user in with an emailed code. Unknown emails get a 404: new accounts go through /api/signup."""
    user = await verify_email_otp(body.email, body.otp_code, db)
    start_session(db, USER_SESSION, request, response, user_id=user["id"])
    return {"user": user}

@app.post("/api/auth/logout", status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def logout(
    request: Request,
    response: Response,
    db: Client = Depends(get_db_connection)
):
    """Ends this browser's session and clears its cookie. Safe to call when already logged out."""
    await end_session(db, USER_SESSION, request, response)
    return {"status": "success"}

@app.post("/api/auth/logout-all", status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def logout_everywhere(
    request: Request,
    response: Response,
    session: dict = Depends(get_user_session),
    db: Client = Depends(get_db_connection)
):
    """Ends every session the user has, on all devices, this one included."""
    await end_all_user_sessions(db, session["user_id"], request, response)
    return {"status": "success"}

@app.get("/api/auth/session", response_model=AuthResponse, status_code=status.HTTP_200_OK, tags=["User Authentication"])
async def get_session(current_user: dict = Depends(get_current_user)):
    """Returns the logged-in user, or 401 without a live session. The frontend checks this before showing user pages."""
    return {"user": current_user}

# --- User Routes ---

@app.get("/api/me/status", response_model=StatusResponse, status_code=status.HTTP_200_OK, tags=["User Operations & Chat"])
async def get_my_status(
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db_connection)
):
    """Polled by the client waiting room to verify current matchmaking status."""
    return await get_user_status(current_user["id"], db)

@app.post("/api/me/unmatch", status_code=status.HTTP_200_OK, tags=["User Operations & Chat"])
async def unmatch_from_room(
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db_connection)
):
    """Allows a user to leave their current active chat room and return to the waiting pool."""
    res = db.table("users").select("room_id").eq("id", current_user["id"]).execute()
    if not res.data or not res.data[0]["room_id"]:
        raise HTTPException(status_code=400, detail="You are not currently in an active room.")
    
    room_id = res.data[0]["room_id"]
    return await deactivate_room(UUID(room_id), db, reason="User left the chat")

@app.delete("/api/me", status_code=status.HTTP_200_OK, tags=["User Operations & Chat"])
async def delete_my_account(
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db_connection)
):
    """Deletes the logged-in user's account for good, ends their chat and logs them out on every device."""
    await delete_user(current_user["id"], db)
    clear_session_cookie(request, response, USER_SESSION)
    return {"status": "success"}

@app.post("/api/user/quiz", status_code=status.HTTP_200_OK, tags=["User Operations & Chat"])
async def submit_user_quiz(
    body: dict[str, str],
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db_connection)
):
    """Allows authenticated users to submit or update their quiz answers."""
    return await submit_quiz(current_user["id"], body, db)

@app.get("/api/rooms/{room_id}/messages", response_model=MessagesListResponse, status_code=status.HTTP_200_OK, tags=["User Operations & Chat"])
async def get_messages(
    room_id: UUID,
    before: UUID | None = Query(None, description="Load messages before this message UUID for pagination"),
    current_user: dict = Depends(get_current_user),
    db: Client = Depends(get_db_connection)
):
    """Fetches up to 50 historical messages for a matched room in ascending order."""
    messages = await get_room_messages(room_id, current_user["id"], before, db)
    return {"messages": messages}

# --- Admin Auth Endpoints ---

@app.post("/api/admin/login", response_model=AdminSessionResponse, status_code=status.HTTP_200_OK, tags=["Admin Authentication"])
async def admin_login(
    body: AdminLoginRequest,
    request: Request,
    response: Response,
    db: Client = Depends(get_db_connection)
):
    """Authenticates the admin against environment variables and starts an admin session cookie."""
    admin_user = os.getenv("ADMIN_USERNAME")
    admin_pwd_hash = os.getenv("ADMIN_PASSWORD_HASH")
    
    if not admin_user or not admin_pwd_hash:
        print("Admin credentials are not configured in environment variables", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin environment setup incomplete"
        )
        
    import hmac

    if not hmac.compare_digest(body.username, admin_user) or not verify_password(body.password, admin_pwd_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials"
        )
        
    start_session(db, ADMIN_SESSION, request, response)
    return {"username": admin_user}

@app.post("/api/admin/logout", status_code=status.HTTP_200_OK, tags=["Admin Authentication"])
async def admin_logout(
    request: Request,
    response: Response,
    db: Client = Depends(get_db_connection)
):
    """Ends this browser's admin session and clears its cookie. Safe to call when already logged out."""
    await end_session(db, ADMIN_SESSION, request, response)
    return {"status": "success"}

@app.get("/api/admin/session", response_model=AdminSessionResponse, status_code=status.HTTP_200_OK, tags=["Admin Authentication"])
async def get_admin_session_info(current_admin: str = Depends(get_current_admin)):
    """Returns the admin username, or 401 without a live admin session."""
    return {"username": current_admin}

# --- Admin Operations Endpoints ---

@app.get("/api/admin/users", response_model=AdminUsersListResponse, status_code=status.HTTP_200_OK, tags=["Admin Control Panel"])
async def get_all_users(
    search: str | None = Query(None, description="Search by email or display name"),
    page: int = Query(1, ge=1, description="Page index"),
    limit: int = Query(50, ge=1, le=100, description="Page size limit"),
    status_filter: str | None = Query(None, alias="status", description="Filter by user status: 'waiting' or 'matched'"),
    current_admin: str = Depends(get_current_admin),
    db: Client = Depends(get_db_connection)
):
    """Lists all users registered in the system (admin only)."""
    return await get_admin_users(search, page, limit, db, status_filter)

@app.post("/api/admin/match", response_model=MatchResponse, status_code=status.HTTP_200_OK, tags=["Admin Control Panel"])
async def match_waiting_users(
    body: MatchRequest,
    current_admin: str = Depends(get_current_admin),
    db: Client = Depends(get_db_connection)
):
    """Matches two waiting users into an active chat room, optionally emailing both of them (admin only)."""
    return await match_users(body.user_a_id, body.user_b_id, db, notify_by_email=body.notify_by_email)

@app.get("/api/admin/email-config", response_model=AdminEmailConfigResponse, status_code=status.HTTP_200_OK, tags=["Admin Control Panel"])
async def get_email_config(
    current_admin: str = Depends(get_current_admin)
):
    """Reports whether SMTP is configured, so the admin panel knows if match emails can be sent (admin only)."""
    return {"enabled": is_email_configured()}

@app.get("/api/admin/rooms", response_model=AdminRoomsListResponse, status_code=status.HTTP_200_OK, tags=["Admin Control Panel"])
async def get_rooms_list(
    status_filter: str = Query("active", description="Filter by status: 'active', 'inactive', or 'all'"),
    current_admin: str = Depends(get_current_admin),
    db: Client = Depends(get_db_connection)
):
    """Lists chat rooms based on their active status (admin only)."""
    return await get_admin_rooms(db, status_filter)

@app.post("/api/admin/rooms/{room_id}/deactivate", status_code=status.HTTP_200_OK, tags=["Admin Control Panel"])
async def deactivate_chat_room(
    room_id: UUID,
    current_admin: str = Depends(get_current_admin),
    db: Client = Depends(get_db_connection)
):
    """Closes an active chat room and resets the status of both users back to waiting (admin only)."""
    return await deactivate_room(room_id, db)

@app.delete("/api/admin/users/{user_id}", status_code=status.HTTP_200_OK, tags=["Admin Control Panel"])
async def delete_user_account(
    user_id: UUID,
    current_admin: str = Depends(get_current_admin),
    db: Client = Depends(get_db_connection)
):
    """Deletes a user for good, with every chat they were in. A partner in an active chat goes back to waiting (admin only)."""
    return await delete_user(user_id, db)

# --- Admin CSV Export Endpoints (Streamed Response) ---

def _sanitize_csv_field(value):
    """Neutralizes CSV/formula injection (OWASP): if a field starts with a character
    that spreadsheet apps (Excel, Sheets) interpret as a formula trigger, prefix it
    with a single quote so it's treated as plain text on open, since fields like
    email, display name, and chat content are user-controlled.
    """
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text

async def export_users_csv_stream():
    """Helper: Streams users from DB as CSV chunks without loading all rows in memory."""
    from app.database import get_db_client
    try:
        db = get_db_client()
    except Exception:
        raise RuntimeError("Database client not initialized")

    import io
    import csv

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["id", "email", "display_name", "status", "created_at", "date_of_birth", "age", "gender", "interested_in", "state", "bio", "single_reason", "quiz_answers"])
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    
    chunk_size = 100
    offset = 0
    while True:
        try:
            res = db.table("users").select("id, email, display_name, status, created_at, quiz_answers, date_of_birth, gender, interested_in, state, bio, single_reason")\
                .order("created_at", desc=True)\
                .range(offset, offset + chunk_size - 1).execute()
        except Exception as e:
            print(f"Error exporting users CSV: {e}", file=sys.stderr)
            break
            
        if not res.data:
            break
            
        for row in res.data:
            quiz = {}
            quiz_data = row["quiz_answers"]
            if quiz_data:
                if isinstance(quiz_data, str):
                    try:
                        quiz = json.loads(quiz_data)
                    except Exception:
                        quiz = {}
                elif isinstance(quiz_data, dict):
                    quiz = quiz_data
            
            created_at_str = row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else (row["created_at"] or "")
            
            writer.writerow([
                str(row["id"]),
                _sanitize_csv_field(row["email"]),
                _sanitize_csv_field(row["display_name"]),
                row["status"],
                created_at_str,
                _sanitize_csv_field(row["date_of_birth"]),
                _sanitize_csv_field(age_from_date_of_birth(row["date_of_birth"])),
                _sanitize_csv_field(row["gender"]),
                _sanitize_csv_field(", ".join(row["interested_in"] or [])),
                _sanitize_csv_field(row["state"]),
                _sanitize_csv_field(row["bio"]),
                _sanitize_csv_field(row["single_reason"]),
                # One JSON column keyed by question, since the quiz questions change over time
                _sanitize_csv_field(json.dumps(quiz, ensure_ascii=False))
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
            
        if len(res.data) < chunk_size:
            break
        offset += chunk_size

@app.get("/api/admin/users/export", tags=["Admin Data Exports"])
async def export_users_csv(current_admin: str = Depends(get_current_admin)):
    """Downloads database users record in CSV format using StreamingResponse (admin only)."""
    return StreamingResponse(
        export_users_csv_stream(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=\"users.csv\""
        }
    )

async def export_messages_csv_stream(room_id: UUID | None):
    """Helper: Streams messaging transcripts from DB as CSV chunks without loading all rows in memory."""
    from app.database import get_db_client
    try:
        db = get_db_client()
    except Exception:
        raise RuntimeError("Database client not initialized")
        
    import io
    import csv
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["id", "room_id", "sender_id", "sender_display_name", "content", "sent_at"])
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    
    chunk_size = 100
    offset = 0
    while True:
        try:
            # We select: id, room_id, sender_id, content, sent_at, users(display_name)
            query = db.table("messages").select(
                "id, room_id, sender_id, content, sent_at, users(display_name)"
            )
            if room_id:
                query = query.eq("room_id", str(room_id))
                
            res = query.order("sent_at", desc=False).range(offset, offset + chunk_size - 1).execute()
        except Exception as e:
            print(f"Error exporting messages CSV: {e}", file=sys.stderr)
            break
            
        if not res.data:
            break
            
        for row in res.data:
            user_data = row.get("users")
            display_name = ""
            if isinstance(user_data, dict):
                display_name = user_data.get("display_name", "")
            elif isinstance(user_data, list) and user_data:
                display_name = user_data[0].get("display_name", "")
                
            sent_at_str = row["sent_at"].isoformat() if hasattr(row["sent_at"], "isoformat") else (row["sent_at"] or "")
            
            writer.writerow([
                str(row["id"]),
                str(row["room_id"]),
                str(row["sender_id"]),
                _sanitize_csv_field(display_name),
                _sanitize_csv_field(row["content"]),
                sent_at_str
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
            
        if len(res.data) < chunk_size:
            break
        offset += chunk_size

@app.get("/api/admin/messages/export", tags=["Admin Data Exports"])
async def export_messages_csv(
    room_id: UUID | None = Query(None, description="Filter export by room UUID"),
    current_admin: str = Depends(get_current_admin)
):
    """Downloads messaging logs in CSV format using StreamingResponse (admin only)."""
    filename = f"messages_{room_id}.csv" if room_id else "messages.csv"
    return StreamingResponse(
        export_messages_csv_stream(room_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\""
        }
    )

# --- WebSocket Room Server ---

@app.websocket("/ws/{room_id}")
async def websocket_room_handler(websocket: WebSocket, room_id: UUID):
    """Establishes real-time duplex chat session inside active matchmaking rooms."""

    async def reject(code: int, reason: str) -> None:
        # Accept first: a close before accept reaches the browser as a failed handshake (code 1006),
        # hiding the 4001/4003 codes the client relies on to stop reconnecting
        await websocket.accept()
        await websocket.close(code=code, reason=reason)

    # Backs up SameSite=Lax: refuse handshakes started by pages on other sites
    if not is_trusted_origin(websocket.headers):
        await reject(4003, "Cross-site connection blocked")
        return

    from app.database import get_db_client
    try:
        db = get_db_client()
    except Exception:
        await websocket.close(code=1011, reason="Database client uninitialized")
        return

    # The browser sends the session cookie with the handshake, so no token ever lands in the URL (or access logs)
    session_token = websocket.cookies.get(USER_SESSION.cookie_name)
    try:
        session = find_session(db, USER_SESSION, session_token)
    except Exception as e:
        print(f"Error loading session in WS for room {room_id}: {e}", file=sys.stderr)
        await websocket.close(code=1011, reason="Database read error")
        return
    if not session:
        await reject(4001, "Not logged in")
        return
    user_id = UUID(session["user_id"])

    # Verify room is active and user is a participant
    try:
        res_room = db.table("rooms").select("user_a, user_b, is_active").eq("id", str(room_id)).execute()
        if not res_room.data:
            await reject(4003, "Active room not found")
            return
        room_row = res_room.data[0]
    except Exception as e:
        print(f"Error querying room {room_id} in WS: {e}", file=sys.stderr)
        await websocket.close(code=1011, reason="Database read error")
        return
            
    if not room_row or not room_row["is_active"]:
        await reject(4003, "Active room not found")
        return

    if room_row["user_a"] != str(user_id) and room_row["user_b"] != str(user_id):
        await reject(4003, "Access to room forbidden")
        return

    # Add connection to registry
    room_key = str(room_id)
    user_key = str(user_id)
    
    # Accept handshake
    await websocket.accept()
    # Lets a logout find and close the sockets its session opened
    websocket.state.session_id = str(session["id"])
    connections[room_key][user_key] = websocket

    # Logouts close their sockets directly, but only in this process; re-checking now and then also
    # catches expiry and logouts handled by another worker
    SESSION_RECHECK_SECONDS = 60
    session_checked_at = time.monotonic()

    # Simple sliding-window rate limit to stop a single connection from spamming
    # unlimited messages/DB writes: at most 10 messages per rolling 5-second window.
    RATE_LIMIT_MAX_MESSAGES = 10
    RATE_LIMIT_WINDOW_SECONDS = 5
    message_timestamps: list[float] = []

    try:
        while True:
            # Await messaging loop
            text_data = await websocket.receive_text()

            if time.monotonic() - session_checked_at >= SESSION_RECHECK_SECONDS:
                session_checked_at = time.monotonic()
                try:
                    session_alive = find_session(db, USER_SESSION, session_token) is not None
                except Exception as e:
                    # Don't cut a conversation off over a transient database error
                    print(f"Error re-checking session in WS for user_id={user_id}: {e}", file=sys.stderr)
                    session_alive = True
                if not session_alive:
                    await websocket.close(code=4001, reason="Session ended")
                    break

            now = time.monotonic()
            message_timestamps[:] = [t for t in message_timestamps if now - t < RATE_LIMIT_WINDOW_SECONDS]
            if len(message_timestamps) >= RATE_LIMIT_MAX_MESSAGES:
                # Silently drop messages sent over the limit instead of processing them
                continue
            message_timestamps.append(now)

            try:
                data = json.loads(text_data)
                content = data.get("content", "")
            except Exception:
                # Invalid payload shape
                continue
                
            if not isinstance(content, str) or not content.strip():
                # Invalid content length/type
                continue
                
            if len(content) > 2000:
                # Truncate or reject
                continue
                
            # Log message to database
            try:
                res_msg = db.table("messages").insert({
                    "room_id": str(room_id),
                    "sender_id": str(user_id),
                    "content": content
                }).execute()
                if not res_msg.data:
                    continue
                msg_row = res_msg.data[0]
            except Exception as e:
                print(f"Error storing message in room={room_id}, sender={user_id}: {e}", file=sys.stderr)
                continue
                
            # Broadcast message to matched room participants
            sent_at = msg_row["sent_at"]
            sent_at_str = sent_at.isoformat() if hasattr(sent_at, "isoformat") else (sent_at or "")
            payload = {
                "type": "message",
                "id": str(msg_row["id"]),
                "sender_id": user_key,
                "content": content,
                "sent_at": sent_at_str
            }
            
            payload_str = json.dumps(payload)
            
            # Send to both users in the room connection list if active
            user_sockets = connections.get(room_key, {})
            for uid_str, ws in list(user_sockets.items()):
                try:
                    await ws.send_text(payload_str)
                except Exception as e:
                    print(f"Failed broadcasting message to user_id={uid_str} in room={room_id}: {e}", file=sys.stderr)
                    
    except WebSocketDisconnect:
        pass
    finally:
        # Only dequeue if the slot still holds *this* socket: after a reconnect the new socket
        # already owns it, and popping it would silently cut that user off from all messages
        room_sockets = connections.get(room_key)
        if room_sockets is not None and room_sockets.get(user_key) is websocket:
            room_sockets.pop(user_key, None)
            if not room_sockets:
                connections.pop(room_key, None)

# --- Frontend (Svelte build copied into app/static by .githooks/pre-push) ---
# Mounted last so every API, docs and WebSocket route above takes precedence.

class SPAStaticFiles(StaticFiles):
    """Serves index.html for unknown page paths (e.g. /admin) so main.js can map them to hash routes."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Missing files (anything with an extension) and unknown /api paths keep a real 404
            is_api_path = path.split(os.sep, 1)[0] == "api"
            if exc.status_code != 404 or os.path.splitext(path)[1] or is_api_path:
                raise
            return await super().get_response("index.html", scope)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/", SPAStaticFiles(directory=STATIC_DIR, html=True), name="frontend")
else:
    print(f"Frontend build not found at {STATIC_DIR}; serving API only", file=sys.stderr)
