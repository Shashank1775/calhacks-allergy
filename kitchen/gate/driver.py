"""GateDriver implementations: FakeGate (prints and records calls) now, the GPIO driver for servo, lights and buzzer later.

Owner: outputs (agents)
Step:  gate and screens
Reads: gate state from service.py (idle | pass | hold | fault, ticket number)
Emits: servo position, red/green light, buzzer
Rules: fail closed: any driver exception means fault

Not built yet.
"""
