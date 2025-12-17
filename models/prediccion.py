"""
Modelos para predicciones de concursos usando LLM
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class PrediccionConcurso(BaseModel):
    """
    Predicción generada por LLM sobre si dos concursos son el mismo
    y cuándo se abrirá la próxima versión.
    """
    es_mismo_concurso: bool = Field(
        ...,
        description="True si los concursos son esencialmente el mismo (solo difieren en año/versión), False si son diferentes"
    )
    
    fecha_predicha: Optional[str] = Field(
        None,
        description="Fecha predicha de apertura en formato YYYY-MM-DD o texto descriptivo (ej: 'marzo 2026', 'primer trimestre 2026')"
    )

    justificacion: str = Field(
        ...,
        min_length=30,
        description="Un párrafo sencillo y claro (máximo 200 palabras) que explique la predicción de forma comprensible. Debe ser conciso y fácil de entender."
    )


class PrediccionResponse(BaseModel):
    """
    Respuesta del LLM con la predicción
    """
    prediccion: PrediccionConcurso


class PrediccionConcursoBatchItem(BaseModel):
    """
    Predicción asociada a un concurso específico dentro de un batch.
    """
    concurso_url: str = Field(
        ...,
        description="URL del concurso al que corresponde esta predicción"
    )
    prediccion: PrediccionConcurso


class PrediccionBatchResponse(BaseModel):
    """
    Respuesta del LLM para un batch de concursos.
    """
    items: List[PrediccionConcursoBatchItem] = Field(
        ...,
        description="Lista de predicciones, una por cada concurso del batch"
    )
