import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from src.demo_ssh import PhenotypeMatcher, SSH_BASELINE

app = FastAPI(title="Cannabis Researcher - ML Phenotype Matching API")

class SampleSubmission(BaseModel):
    sample_id: str
    lineage: Optional[str] = "Unknown"
    thc: Optional[float] = 0.0
    terpenes: Optional[List[str]] = []
    extracted_visuals: Optional[List[str]] = []
    description: Optional[str] = ""

@app.get("/")
def read_root():
    return {"message": "Welcome to the Cannabis Researcher API"}

@app.get("/canonical/{strain_name}")
def get_canonical_strain(strain_name: str):
    # Currently only mocked for Super Silver Haze
    if strain_name.lower() in ["super silver haze", "ssh"]:
        return {
            "name": SSH_BASELINE.name,
            "genetics": SSH_BASELINE.genetics,
            "thc_range": SSH_BASELINE.thc_range,
            "cbd_range": SSH_BASELINE.cbd_range,
            "dominant_terpenes": SSH_BASELINE.dominant_terpenes,
            "visual_traits": SSH_BASELINE.visual_traits,
            "nlp_keywords": SSH_BASELINE.nlp_keywords
        }
    raise HTTPException(status_code=404, detail="Canonical strain not found in database.")

@app.post("/verify/{strain_name}")
def verify_sample(strain_name: str, sample: SampleSubmission):
    """
    Submit a sample to be verified against a canonical strain baseline.
    Returns a probabilstic matching checklist.
    """
    if strain_name.lower() not in ["super silver haze", "ssh"]:
        raise HTTPException(status_code=404, detail="Currently only Super Silver Haze baseline is loaded for verification.")
        
    report = PhenotypeMatcher.generate_report(sample.model_dump(), SSH_BASELINE)
    return {
        "target_baseline": SSH_BASELINE.name,
        "sample_id": sample.sample_id,
        "verification_checklist": report
    }
