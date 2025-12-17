import logging
from datetime import datetime

from config.sites import SEED_URLS
from utils.api_key_manager import APIKeyManager
from services.extraction_service import ExtractionService
from services.prediction_service import PredictionService


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("daily_anid")

    api_key_manager = APIKeyManager()
    extraction_service = ExtractionService(api_key_manager=api_key_manager)
    prediction_service = PredictionService(api_key_manager=api_key_manager)

    urls = SEED_URLS.get("ANID", [])
    if not urls:
        logger.error("No hay URLs semilla para ANID.")
        return

    logger.info("Iniciando scraping diario ANID...")
    concursos = extraction_service.extract_from_urls(
        urls=urls,
        follow_pagination=True,
        max_pages=2
    )
    logger.info(f"Scraping ANID completado: {len(concursos)} concursos extraídos")

    logger.info("Iniciando predicciones para ANID...")
    pred_result = prediction_service.generate_predictions(site="anid.cl")
    if isinstance(pred_result, dict):
        logger.info(f"Predicciones ANID completadas. Stats: {pred_result.get('stats', {})}")
    else:
        logger.info(f"Predicciones ANID completadas: {pred_result}")


if __name__ == "__main__":
    main()

