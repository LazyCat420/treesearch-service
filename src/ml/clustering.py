"""
clustering.py
-------------
Machine learning engine to group similar plant pictures (using CLIP / color histograms)
and correlate cultivars using terpene, effect, and genetic data.
"""
import logging
import hashlib
import re
from typing import Any
import numpy as np
from sqlalchemy import select
from sklearn.cluster import KMeans

logger = logging.getLogger(__name__)

# Check for Pillow and HTTPX dependencies
HAS_ML_LIBRARIES = False
try:
    from PIL import Image
    import httpx
    import base64
    import asyncio
    HAS_ML_LIBRARIES = True
except ImportError:
    HAS_ML_LIBRARIES = False

def get_vllm_endpoints() -> dict[str, str]:
    """Retrieve Jetson and DGX Spark VLLM URLs from vault env or defaults."""
    import os
    from pathlib import Path
    
    endpoints = {
        "JETSON_VLLM_URL": os.getenv("JETSON_VLLM_URL", "http://10.0.0.30:8000"),
        "DGX_SPARK_VLLM_URL": os.getenv("DGX_SPARK_VLLM_URL", "http://10.0.0.141:8000")
    }
    
    # Locate vault-service/.env to read URLs dynamically
    curr = Path(__file__).resolve()
    for parent in curr.parents:
        vault_env = parent / "vault-service" / ".env"
        if vault_env.exists():
            try:
                with open(vault_env, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k in ["JETSON_VLLM_URL", "DGX_SPARK_VLLM_URL"]:
                                endpoints[k] = v
                break
            except Exception as e:
                logger.warning(f"Failed to parse vault .env: {e}")
                
    return endpoints

async def is_budding_plant_image(image_url: str) -> bool:
    """Download image and use remote vision model on Jetson/DGX Spark to verify if it depicts a budding plant."""
    if not HAS_ML_LIBRARIES:
        return True
    try:
        from io import BytesIO
        
        # 1. Download the image
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(image_url)
            if resp.status_code != 200:
                logger.warning(f"Failed to download image for VLM classification from {image_url}: status {resp.status_code}")
                return True  # Fallback to saving it
            
            # 2. Resize image to max 512x512 using Pillow to optimize bandwidth/VRAM/speed
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img.thumbnail((512, 512))
            
            # 3. Convert image to base64
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            b64_img = f"data:image/jpeg;base64,{img_str}"
            
        # 4. Prepare payload for vLLM vision model
        endpoints = get_vllm_endpoints()
        prompt = "Does this image depict a budding cannabis plant or a close-up of a cannabis flower/bud? Answer only Yes or No."
        
        # Attempt Jetson first, then fallback to DGX Spark
        urls_to_try = [
            ("Jetson", endpoints["JETSON_VLLM_URL"], "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"),
            ("DGX Spark", endpoints["DGX_SPARK_VLLM_URL"], "Qwen/Qwen3.5-122B-A10B-FP8")
        ]
        
        for name, base_url, model in urls_to_try:
            url = f"{base_url.rstrip('/')}/v1/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": b64_img}},
                            {"type": "text", "text": prompt}
                        ]
                    }
                ],
                "max_tokens": 512,
                "temperature": 0.0
            }
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        data = r.json()
                        content = data["choices"][0]["message"]["content"]
                        if content:
                            content_lower = content.lower().strip()
                            logger.info(f"VLM classification from {name} for {image_url}: {content_lower.replace('\n', ' ')}")
                            if "yes" in content_lower:
                                return True
                            elif "no" in content_lower:
                                return False
                            # Fallback if answer is ambiguous but successful response
                            return True
            except Exception as e:
                logger.warning(f"VLM classification failed on {name} ({url}): {e}")
                
        # If both endpoints fail, default to True so we don't drop images
        logger.warning(f"All VLM endpoints failed or timed out for {image_url}. Defaulting to True.")
        return True
            
    except Exception as e:
        logger.warning(f"VLM pipeline failed for {image_url}: {e}")
        return True

async def classify_images_batch(urls: list[str], batch_size: int = 15) -> dict[str, bool]:
    """Classify a list of image URLs concurrently in batches using a Semaphore."""
    if not urls:
        return {}
    
    sem = asyncio.Semaphore(batch_size)
    
    async def worker(url: str):
        async with sem:
            res = await is_budding_plant_image(url)
            return url, res
            
    tasks = [worker(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return dict(results)

async def extract_image_embedding(image_url: str) -> list[float]:
    """Download image and extract feature vector (color histogram fallback)."""
    if HAS_ML_LIBRARIES:
        # Fallback to local image processing (color histogram)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(image_url)
                if resp.status_code == 200:
                    from io import BytesIO
                    img = Image.open(BytesIO(resp.content)).convert("RGB")
                    img = img.resize((64, 64))
                    hist = img.histogram() # RGB components (768 elements)
                    arr = np.interp(np.linspace(0, len(hist) - 1, 512), np.arange(len(hist)), hist)
                    norm = np.linalg.norm(arr)
                    if norm > 0:
                        arr = arr / norm
                    return arr.tolist()
        except Exception as e:
            logger.warning(f"Color histogram extraction failed for {image_url}: {e}")

    # Ultimate deterministic hashing fallback if no libraries or download failed
    return get_fallback_features(image_url)

def get_fallback_features(image_url: str) -> list[float]:
    """Deterministic hashing vector mapping image URL to a normalized feature vector."""
    h = hashlib.sha256(image_url.encode()).digest()
    feats = []
    for i in range(16):
        sub_h = hashlib.sha256(h + bytes([i])).digest()
        for b in sub_h:
            feats.append(float(b) / 255.0)
    arr = np.array(feats)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()

async def run_image_clustering(session) -> int:
    """Find all unclustered images, generate embeddings, and assign cluster labels."""
    from src.models.orm import ObservationImageORM
    
    # 1. Fetch images without embeddings or cluster_ids
    stmt = select(ObservationImageORM).where(ObservationImageORM.cluster_id == None)
    images = (await session.execute(stmt)).scalars().all()
    if not images:
        return 0
        
    logger.info(f"Extracting features for {len(images)} images...")
    
    # 2. Extract feature vectors
    for img in images:
        if not img.embedding:
            img.embedding = await extract_image_embedding(img.image_url)
            
    # 3. Apply KMeans clustering on all images in database to assign cluster_ids
    stmt_all = select(ObservationImageORM).where(ObservationImageORM.embedding != None)
    all_images = (await session.execute(stmt_all)).scalars().all()
    if len(all_images) < 2:
        for img in all_images:
            img.cluster_id = "cluster_0"
        await session.commit()
        return len(images)
        
    embeddings = []
    for img in all_images:
        emb = img.embedding
        if len(emb) != 512:
            emb_arr = np.array(emb, dtype=float)
            emb_arr = np.interp(np.linspace(0, len(emb) - 1, 512), np.arange(len(emb)), emb_arr)
            norm = np.linalg.norm(emb_arr)
            if norm > 0:
                emb_arr = emb_arr / norm
            emb = emb_arr.tolist()
            img.embedding = emb
        embeddings.append(emb)
        
    X = np.array(embeddings)
    n_clusters = max(2, min(len(X) // 2, 15))
    
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)
    
    for img, label in zip(all_images, labels):
        img.cluster_id = f"cluster_{label}"
        
    await session.commit()
    logger.info(f"Successfully clustered {len(all_images)} images into {n_clusters} groups.")
    return len(images)

def calculate_cultivar_similarity(
    strain_a: dict,
    strain_b: dict,
    genetic_distance: float | None = 1.0,
) -> float:
    """Calculate combined similarity between two strains (0.0 to 1.0, where 1.0 is identical).
    Combines:
    - Genetics distance (if present)
    - Terpene profile correlation
    - Effects tags Jaccard correlation
    - Plant picture cluster sharing
    """
    scores = []
    weights = []
    
    # 1. Genetics correlation (1 - distance)
    if genetic_distance is not None:
        scores.append(1.0 - genetic_distance)
        weights.append(0.4)
        
    # 2. Terpene correlation (cosine similarity of terpene profiles)
    t_a = strain_a.get("terpenes", {})
    t_b = strain_b.get("terpenes", {})
    if t_a and t_b:
        keys = set(t_a.keys()).union(set(t_b.keys()))
        v_a = [t_a.get(k, 0.0) for k in keys]
        v_b = [t_b.get(k, 0.0) for k in keys]
        norm_a = np.linalg.norm(v_a)
        norm_b = np.linalg.norm(v_b)
        if norm_a > 0 and norm_b > 0:
            cosine = np.dot(v_a, v_b) / (norm_a * norm_b)
            scores.append(float(cosine))
            weights.append(0.3)
            
    # 3. Effects similarity (Jaccard similarity of effects tags)
    e_a = set(strain_a.get("effects", []))
    e_b = set(strain_b.get("effects", []))
    if e_a or e_b:
        intersection = len(e_a.intersection(e_b))
        union = len(e_a.union(e_b))
        jaccard = intersection / union if union > 0 else 0.0
        scores.append(jaccard)
        weights.append(0.15)
        
    # 4. Shared image clusters (Jaccard similarity of image clusters)
    c_a = set(strain_a.get("image_clusters", []))
    c_b = set(strain_b.get("image_clusters", []))
    if c_a or c_b:
        intersection = len(c_a.intersection(c_b))
        union = len(c_a.union(c_b))
        jaccard = intersection / union if union > 0 else 0.0
        scores.append(jaccard)
        weights.append(0.15)
        
    if not scores:
        return 0.0
        
    # Weighted average
    total_weight = sum(weights)
    weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
    return round(weighted_score, 4)
