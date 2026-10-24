"""GateDriver implementations: ConsoleGate and MockGPIOGate now, the GPIO driver for servo, lights and buzzer in M2.

Owner: outputs (agents)
Step:  pass gate and voice (outputs)
Reads: gate state from service.py (idle | pass | hold | fault, ticket number)
Emits: servo position, red/green light, buzzer (GateDriver)
Rules: 1 any driver exception means fault
Tasks: outputs-03; GPIO driver in M2 (hardware)

Not built yet (phase gate: PLANNING).
"""
