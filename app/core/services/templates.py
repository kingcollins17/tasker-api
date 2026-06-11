from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Locate the templates directory: app/templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # Resolves to app/
TEMPLATES_DIR = BASE_DIR / "templates"

# Create the templates directory if it doesn't exist to avoid loading errors
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# Initialize the Jinja2 environment
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"])
)

def render_template(template_name: str, context: dict) -> str:
    """Loads and renders a Jinja2 template file with a given context.

    Args:
        template_name: The path of the template file under app/templates/
        context: Key-value pairs to inject into the template.

    Returns:
        str: The rendered template string.
    """
    template = jinja_env.get_template(template_name)
    return template.render(**context)
