"""
Utilidades generales
"""

from .date_parser import parse_date, is_past_date, estimate_next_opening
from .file_manager import (
    save_results, 
    load_results, 
    export_to_csv,
    save_raw_crawl_results,
    save_debug_info,
    save_debug_info_scraping,
    save_debug_info_repair,
    save_debug_info_predictions,
    save_debug_info_individual_prediction,
    save_predictions, 
    load_predictions,
    delete_prediction,
    delete_predictions_by_urls,
    clear_predictions,
    save_unpredictable_concursos,
    load_unpredictable_concursos,
    clear_unpredictable_concursos
)
from .api_key_manager import APIKeyManager
from .history_manager import HistoryManager
from .concurso_similarity import (
    normalize_concurso_name,
    extract_year_from_name,
    calculate_name_similarity,
    are_similar_concursos,
    find_similar_concurso_in_list
)
from .url_extractor import (
    extract_concurso_urls_from_html,
    match_concurso_to_url
)
from .anid_previous_concursos import (
    extract_previous_concursos_from_html,
    format_previous_concursos_for_prediction
)

__all__ = [
    "parse_date",
    "is_past_date", 
    "estimate_next_opening",
    "save_results",
    "load_results",
    "export_to_csv",
    "save_raw_crawl_results",
    "save_debug_info",
    "save_debug_info_scraping",
    "save_debug_info_repair",
    "save_debug_info_predictions",
    "save_debug_info_individual_prediction",
    "APIKeyManager",
    "HistoryManager",
    "save_predictions",
    "load_predictions",
    "delete_prediction",
    "delete_predictions_by_urls",
    "clear_predictions",
    "save_unpredictable_concursos",
    "load_unpredictable_concursos",
    "clear_unpredictable_concursos",
    "normalize_concurso_name",
    "extract_year_from_name",
    "calculate_name_similarity",
    "are_similar_concursos",
    "find_similar_concurso_in_list",
    "extract_concurso_urls_from_html",
    "match_concurso_to_url",
    "extract_previous_concursos_from_html",
    "format_previous_concursos_for_prediction"
]

