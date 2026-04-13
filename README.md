Master Agent v3.0 is a multi-agent autonomous system designed for 24/7 market surveillance, macro-economic analysis, and strategic portfolio management. The system integrates real-time data harvesting with advanced LLM (Large Language Model) reasoning to provide institutional-grade investment insights.

Key Architecture:
Scout Module (Tactical Intelligence): Continuously monitors global financial news outlets (Yahoo Finance, CNBC, MarketWatch) via multi-source RSS feeds. It uses AI to distinguish between market noise and critical signals (e.g., impact on NVDA, ETH, or Energy sectors).

HQ Consilium (Strategic Analysis): Simulates a high-level investment committee consisting of perspectives from Goldman Sachs (Aggressive/Financials), J.P. Morgan (Macro Risk), and Bridgewater Associates/Ray Dalio (Long-term debt cycles).

Automated Reporting: Features a scheduled dispatch system that generates comprehensive "Morning Open" and "Evening Close" reports based on the last 24 hours of global market activity.

Telegram Integration: A unified command-and-control interface that delivers real-time alerts and on-demand strategic reports directly to the user.

Tech Stack:
Language: Python 3.x

AI Engine: Google Gemini 2.5 Flash (Generative AI)

Deployment: Cloud-native (Render/PythonAnywhere) for 24/7 uptime.

Libraries: google-generativeai, feedparser, schedule, threading, requests.
