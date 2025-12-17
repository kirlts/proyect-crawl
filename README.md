# 🔍 Buscador de Oportunidades de Financiamiento (MVP)

Herramienta de validación rápida para centralizar oportunidades de financiamiento para investigadores académicos en Chile.

## 🎯 Características

- **Scraping Inteligente**: Usa Crawl4AI para manejar sitios dinámicos con JavaScript
- **Extracción con IA**: Utiliza Gemini Flash 2.5 para extraer información estructurada
- **Predicción de Aperturas**: Estima fechas de próxima apertura basándose en patrones históricos
- **Interfaz Simple**: UI con Streamlit para fácil uso
- **Persistencia Local**: Guarda resultados en JSON y CSV

## 📋 Requisitos

- Python 3.8+
- API Key de Google AI Studio (Gemini)

## 🚀 Instalación

1. **Clonar o descargar el proyecto**

2. **Instalar dependencias:**
```bash
pip install -r requirements.txt
```

3. **Configurar Crawl4AI:**
```bash
crawl4ai-setup
```

4. **Verificar instalación (opcional):**
```bash
crawl4ai-doctor
```

## 🔧 Configuración

1. **Obtener API Key de Gemini:**
   - Ve a https://aistudio.google.com/
   - Crea un proyecto y obtén tu API key

2. **Seleccionar Modelo LLM:**
   - En la interfaz Streamlit, usa el selector de modelos en la barra lateral
   - **Recomendado para Free Tier:** `gemini-2.5-flash-lite` (más económico)
   - Todos los modelos disponibles están marcados con 🆓 si son compatibles con Free Tier

3. **Configurar URLs (opcional):**
   - Edita `config.py` para agregar o modificar URLs semilla

## 💻 Uso

1. **Ejecutar la aplicación:**
```bash
streamlit run main.py
```

2. **En la interfaz:**
   - Ingresa tu API Key de Gemini en la barra lateral
   - **Selecciona el modelo LLM** (recomendado: Flash Lite para free tier)
   - Selecciona los sitios a procesar o ingresa URLs personalizadas
   - Presiona "Iniciar Crawling"
   - Espera a que se procesen las URLs
   - Filtra y explora los resultados
   - Guarda o exporta los datos

## 📁 Estructura del Proyecto

```
proyect-crawl/
├── main.py                 # Aplicación Streamlit
├── config.py              # Configuración centralizada
├── requirements.txt       # Dependencias
├── crawler/              # Módulo de scraping
│   ├── scraper.py
│   └── markdown_processor.py
├── llm/                  # Módulo de integración LLM
│   ├── gemini_client.py
│   └── prompts.py
├── utils/                # Utilidades
│   ├── date_parser.py
│   └── file_manager.py
└── data/                 # Datos (se crea automáticamente)
    ├── raw/
    ├── processed/
    └── cache/
```

## 🎨 Sitios Objetivo

- **ANID**: anid.cl (excluyendo capital humano)
- **Centro Estudios MINEDUC**: centroestudios.mineduc.cl
- **CNA**: cnachile.cl
- **DFI MINEDUC**: dfi.mineduc.cl

## 📊 Formato de Datos

Cada concurso extraído contiene:

- `nombre`: Nombre del concurso (REQUERIDO)
- `organismo`: Organismo que administra el concurso (REQUERIDO, ej: "ANID", "MINEDUC", "CNA")
- `fecha_apertura`: Fecha de apertura normalizada (formato: YYYY-MM-DD)
- `fecha_cierre`: Fecha de cierre normalizada (formato: YYYY-MM-DD)
- `fecha_apertura_original`: Texto original de la fecha de apertura
- `financiamiento`: Monto o tipo de financiamiento disponible
- `url`: URL de origen donde se encontró el concurso (REQUERIDO)
- `estado`: "Abierto", "Cerrado", "Suspendido" o "Próximo" (calculado automáticamente)
- `descripcion`: Resumen breve del concurso
- `subdireccion`: Subdirección o área del organismo (ej: "Capital Humano", "Investigación Aplicada")
- `predicted_opening`: Fecha estimada de próxima apertura (si aplica, generada por análisis histórico)

## 🔍 Filtros Disponibles

- **Todos**: Muestra todos los concursos
- **Abiertos Ahora**: Solo concursos actualmente abiertos
- **Próxima Apertura**: Concursos con fecha estimada de apertura
- **Cerrados**: Solo concursos cerrados

## 💾 Exportación

- **JSON**: Guarda resultados completos en formato JSON
- **CSV**: Exporta a CSV para análisis en Excel/Google Sheets

## ⚠️ Notas

- Este es un **MVP** (Minimum Viable Product)
- La predicción de aperturas es una estimación basada en patrones
- Algunos sitios pueden requerir ajustes en la configuración de scraping
- El procesamiento puede tardar varios minutos dependiendo del número de URLs

## 🐛 Solución de Problemas

**Error al instalar Crawl4AI:**
- Ejecuta `crawl4ai-setup` y sigue las instrucciones
- En Linux, puede requerir dependencias del sistema

**Timeout en scraping:**
- Aumenta `page_timeout` en `config.py`
- Algunos sitios pueden estar lentos o inaccesibles

**Error con Gemini (429 Quota Exceeded):**
- Verifica que tu API key sea válida
- **Usa un modelo compatible con Free Tier** (marcados con 🆓)
- **Recomendado:** `gemini-2.5-flash-lite` o `gemini-2.5-flash-lite-preview-09-2025`
- Evita modelos experimentales que no aparecen en la documentación oficial
- Revisa los límites de cuota en Google AI Studio
- Si usas un modelo experimental, puede que no esté disponible en free tier

## 📝 Licencia

Este es un proyecto MVP para uso interno.

