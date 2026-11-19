<div align="center">

# 🔥 PyForge

**A collection of hands-on, real-world Python projects covering system monitoring, file integrity automation, log streaming, database pipelines, and backend utilities.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![MySQL](https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white)](https://www.mysql.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

[Explore Projects](#-project-showcase) • [Tech Stack](#-technologies--libraries) • [Getting Started](#-getting-started) • [Author](#-author)

</div>

---

## 📖 Overview

**PyForge** is a practical engineering workspace containing production-inspired Python applications built from scratch. Each project focuses on solving real operational challenges—including live system telemetry, file integrity verification, real-time log ingestion, data quality validation, and backend API automation.

---

## 🛠️ Technologies & Libraries

| Category | Tools & Libraries |
| :--- | :--- |
| **Core Language** | Python 3.10+ (`asyncio`, `pathlib`, `hashlib`, `re`, `collections`) |
| **System & File Automation** | `psutil`, `watchdog` |
| **CLI & Terminal UI** | `rich`, `argparse`, `typer` |
| **Databases & Persistence** | SQLite3, MySQL (`mysql-connector-python`) |
| **Backend & Webhooks** | FastAPI, `requests`, `httpx` |
| **Data Validation & Analytics** | Pandas, NumPy |

---

## 📂 Repository Structure

```text
PyForge/
├── SysPulse - Live Process & System Monitor/   # Real-time CPU/RAM telemetry & SQLite logger
│   ├── main.py
│   ├── monitor.py
│   ├── database.py
│   └── requirements.txt
├── FileSentinel/                               # Directory watcher & SHA-256 integrity auditor
├── LogStreamGuard/                             # Live log tailing, regex alerting & webhook notifier
├── DataQualityValidator/                       # Automated SQL & Python dataset validation pipeline
└── README.md
