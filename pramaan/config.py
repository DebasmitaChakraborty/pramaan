import os
from google import genai

def get_genai_client() -> genai.Client:
    """Initializes Gemini client using Vertex AI configuration (P8)."""
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"
    project = os.getenv("GCP_PROJECT_ID", "pramaan-506517")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    if use_vertex:
        return genai.Client(vertexai=True, project=project, location=location)
    else:
        # Fallback to AI Studio key if explicitly disabled
        return genai.Client()
