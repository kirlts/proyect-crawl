"""
Modelo de datos para concursos de financiamiento

Define la estructura estándar de un concurso con los campos requeridos en el sistema
(**NO** necesariamente los que debe devolver el LLM):
- Nombre del concurso
- Fechas de apertura/cierre
- Organismo
- Financiamiento
- URL (siempre obtenida programáticamente desde el HTML, nunca confiando en el LLM)
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime


class Concurso(BaseModel):
    """
    Modelo de datos para un concurso de financiamiento.
    
    Campos requeridos a nivel de sistema (la URL NO debe ser generada por el LLM):
    - nombre: Nombre del concurso
    - fecha_apertura: Fecha de apertura del concurso
    - fecha_cierre: Fecha de cierre del concurso
    - organismo: Organismo que administra el concurso
    - financiamiento: Monto o tipo de financiamiento
    - url: URL de origen del concurso
    """
    
    # Campos requeridos
    nombre: str = Field(..., description="Nombre completo del concurso")
    fecha_apertura: Optional[str] = Field(None, description="Texto original de la fecha de apertura tal como aparece en la página (ej: '10 de diciembre, 2025'). NO normalices a formato YYYY-MM-DD.")
    fecha_cierre: Optional[str] = Field(None, description="Texto original de la fecha de cierre tal como aparece en la página (ej: '19 de marzo, 2026 - 17:00'). NO normalices a formato YYYY-MM-DD.")
    organismo: str = Field(..., description="Organismo que administra el concurso (ej: ANID, MINEDUC, CNA)")
    financiamiento: Optional[str] = Field(None, description="Monto o tipo de financiamiento disponible. Busca activamente montos, rangos, o menciones de presupuesto.")
    # URL de origen del concurso. Siempre se obtiene programáticamente desde el HTML,
    # nunca se confía en la URL generada por el LLM.
    url: str = Field(..., description="URL de origen donde se encontró el concurso (extraída del HTML, no del LLM)")
    
    # Campos opcionales adicionales (para compatibilidad y enriquecimiento)
    estado: Optional[str] = Field(None, description="Estado del concurso calculado automáticamente: 'Abierto', 'Cerrado', 'Suspendido' o 'Próximo'. NO debe ser calculado por el LLM, se calcula determinísticamente desde las fechas o detección de 'suspendido' en URL/contenido.")
    fecha_apertura_original: Optional[str] = Field(None, description="Texto original de la fecha de apertura")
    descripcion: Optional[str] = Field(None, description="Descripción breve del concurso")
    predicted_opening: Optional[str] = Field(None, description="Fecha estimada de próxima apertura (si está cerrado)")
    subdireccion: Optional[str] = Field(None, description="Subdirección o área del organismo (ej: 'Capital Humano', 'Investigación Aplicada', 'Redes, Estrategia y Conocimiento'). El nombre puede variar según el sitio.")
    
    # Metadatos
    extraido_en: Optional[str] = Field(None, description="Fecha y hora de extracción (ISO format)")
    fuente: Optional[str] = Field(None, description="Fuente de donde se extrajo (ej: 'anid.cl')")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nombre": "Fondo Nacional de Desarrollo Científico y Tecnológico",
                "fecha_apertura": "2025-01-15",
                "fecha_cierre": "2025-03-31",
                "organismo": "ANID",
                "financiamiento": "Hasta $50.000.000",
                        "url": "https://anid.cl/concursos/fondecyt/",
                "estado": "Abierto",
                "descripcion": "Fondo para proyectos de investigación científica"
            }
        }
    )


class ConcursoResponse(BaseModel):
    """
    Modelo para la respuesta de extracción con lista de concursos.
    
    Usado por los extractores para retornar múltiples concursos en una sola respuesta.
    """
    concursos: List[Concurso] = Field(default_factory=list, description="Lista de concursos extraídos")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "concursos": [
                    {
                        "nombre": "Fondo Nacional de Desarrollo Científico y Tecnológico",
                        "fecha_apertura": "2025-01-15",
                        "fecha_cierre": "2025-03-31",
                        "organismo": "ANID",
                        "financiamiento": "Hasta $50.000.000",
                        "url": "https://anid.cl/concursos/fondecyt/"
                    }
                ]
            }
        }
    )

