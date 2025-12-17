"""
Prompts y templates para la extracción de concursos con Gemini
"""

SYSTEM_PROMPT = """Eres un analista experto en fondos de financiamiento para investigación académica en Chile. 
Tu tarea es extraer información estructurada sobre concursos y oportunidades de financiamiento desde contenido web.

Debes ser preciso, exhaustivo y seguir exactamente el esquema JSON proporcionado."""

EXTRACTION_PROMPT_TEMPLATE = """Analiza el siguiente contenido markdown y extrae TODOS los concursos u oportunidades de financiamiento que encuentres.

Para cada concurso, extrae:

1. **nombre** (REQUERIDO): Nombre completo del concurso
2. **fecha_apertura**: Texto original tal como aparece (ej: "10 de diciembre, 2025"). Busca "Apertura:", "Inicio:", "Desde:". Si no encuentras, usa null.
3. **fecha_cierre**: Texto original tal como aparece (ej: "19 de marzo, 2026 - 17:00"). Busca "Cierre:", "Fecha de cierre:", "Hasta:", "Vence:". Incluye hora si está presente. Si no encuentras, usa null.
4. **organismo** (REQUERIDO): Organismo administrador (ej: "ANID", "MINEDUC", "CNA"). Infiere desde contexto si no está explícito.
5. **financiamiento**: Monto o tipo disponible. Busca "monto", "financiamiento", "presupuesto", "$", "hasta", "entre", "máximo", "mínimo". Si no encuentras, usa null.
6. **descripcion** (opcional): Resumen breve del concurso
7. **subdireccion** (opcional): Subdirección o área del organismo. Para ANID busca: "Capital Humano", "Centros e investigación asociativa", "Investigación Aplicada", "Proyectos de Investigación", "Redes, Estrategia y Conocimiento". Para otros sitios, busca categorías o áreas similares.

IMPORTANTE:
- Extrae el TEXTO ORIGINAL de las fechas tal como aparecen en el contenido
- Si encuentras múltiples concursos, extrae TODOS
- Si NO encuentras ningún concurso, retorna: {{"concursos": []}}
- Los campos "nombre" y "organismo" son OBLIGATORIOS. Si faltan, no incluyas ese concurso.

CONTENIDO:
{markdown}
"""


def get_system_prompt() -> str:
    """Retorna el prompt del sistema"""
    return SYSTEM_PROMPT


def get_extraction_prompt(markdown: str) -> str:
    """
    Genera el prompt de extracción para un markdown específico.
    
    Nota: Las URLs se asignan programáticamente después, no se incluyen en el prompt.
    
    Args:
        markdown: Contenido markdown a analizar
        
    Returns:
        Prompt completo para la extracción
    """
    return EXTRACTION_PROMPT_TEMPLATE.format(markdown=markdown)

