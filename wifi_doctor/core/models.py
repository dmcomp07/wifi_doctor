from dataclasses import dataclass

@dataclass
class Finding:
    title: str
    severity: str
    details: str
    recommendation: str
