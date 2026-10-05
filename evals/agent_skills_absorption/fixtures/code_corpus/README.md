"""Test corpus fixture for CG0 (Code Graph baseline).

A miniature realistic multi-language codebase containing:
- Python module: math_service.py (class Calculator, add, subtract)
- Python module: app_service.py (imports math_service, calls add, subclasses BaseService)
- Python module: base.py (class BaseService)
- TypeScript module: types.ts (User, MathResult interfaces/types)
- TypeScript module: client.ts (imports types, calls apiClient, defines class ApiService)
- JavaScript module: index.js (imports client, calls startApp)
"""
