from flask import Flask, render_template, abort, jsonify, request, session
from flask_caching import Cache
from flask_wtf.csrf import CSRFProtect, CSRFError
import pandas as pd
import matplotlib.pyplot as plt
import os
from pathlib import Path
import logging
from datetime import datetime
import bleach

# Configure logging for debugging and monitoring
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('app.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# App configuration using environment variables for flexibility
class Config:
    DEBUG = os.environ.get('FLASK_DEBUG', 'False') == 'True'
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key')  # Ensure this is a strong, unique key in production
    STATIC_FOLDER = 'static'
    CHART_UPDATE_INTERVAL = int(os.environ.get('CHART_INTERVAL', '3600'))  # Update chart every hour
    CSV_FILE_PATH = os.environ.get('CSV_FILE_PATH', 'recipes.csv')
    SESSION_COOKIE_SECURE = True  # Ensure cookies are only sent over HTTPS in production
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookies

# Initialize Flask app
app = Flask(__name__)  # Templates folder is 'templates' by default
app.config.from_object(Config)

# Add custom dateformat filter (if using Option 2)
@app.template_filter('dateformat')
def dateformat(value, format_string):
    if value == 'now':
        return datetime.now().strftime(format_string)
    return value

# Initialize CSRF protection
csrf = CSRFProtect()
try:
    csrf.init_app(app)
    logger.info("CSRF protection initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize CSRF protection: {e}")
    raise

# Initialize caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Data management class for handling recipes
class RecipeManager:
    def __init__(self, data=None, csv_file=None):
        self.csv_file = csv_file
        try:
            if csv_file and os.path.exists(csv_file):
                # Check if the file is empty
                if os.path.getsize(csv_file) == 0:
                    logger.warning(f"CSV file {csv_file} is empty. Using default data.")
                    raise ValueError("CSV file is empty")
                self.df = pd.read_csv(csv_file)
                # Verify required columns
                required_columns = ["Nosaukums", "Sastāvdaļas", "Pagatavošana"]
                if not all(col in self.df.columns for col in required_columns):
                    logger.warning(f"CSV file {csv_file} missing required columns. Using default data.")
                    raise ValueError("Missing required columns in CSV")
            elif data:
                self.df = pd.DataFrame(data)
            else:
                logger.info("No CSV file provided. Using default data.")
                self.df = pd.DataFrame({
                    "Nosaukums": ["Pankūkas", "Karbonāde", "Zupa"],
                    "Sastāvdaļas": ["Milti, Piens, Olas, Cukurs", "Cūkgaļa, Ola, Rīvmaize, Garšvielas", "Burkāni, Kartupeļi, Gaļa, Sīpoli"],
                    "Pagatavošana": ["Samaisa sastāvdaļas un cep uz pannas.", "Gaļu panē un cep uz pannas.", "Sastāvdaļas vāra kopā līdz gatavs."],
                })
        except Exception as e:
            logger.error(f"Error initializing RecipeManager: {e}")
            self.df = pd.DataFrame({
                "Nosaukums": ["Pankūkas", "Karbonāde", "Zupa"],
                "Sastāvdaļas": ["Milti, Piens, Olas, Cukurs", "Cūkgaļa, Ola, Rīvmaize, Garšvielas", "Burkāni, Kartupeļi, Gaļa, Sīpoli"],
                "Pagatavošana": ["Samaisa sastāvdaļas un cep uz pannas.", "Gaļu panē un cep uz pannas.", "Sastāvdaļas vāra kopā līdz gatavs."],
            })  # Fallback to default data instead of empty DataFrame
        self.last_chart_update = None
    
    def save_to_csv(self):
        """Save current recipes to CSV for persistence"""
        try:
            self.df.to_csv(self.csv_file, index=False)
            logger.info("Recipes saved to CSV")
        except Exception as e:
            logger.error(f"Error saving to CSV: {e}")

    def get_all_recipes(self):
        """Return all recipes as a list of dictionaries"""
        return self.df.to_dict(orient='records')
    
    def get_recipe_by_index(self, index):
        """Get a recipe by its index in the DataFrame"""
        if 0 <= index < len(self.df):
            return self.df.iloc[index].to_dict()
        return None
    
    def get_recipe_by_name(self, name):
        """Get a recipe by its name"""
        recipe = self.df[self.df["Nosaukums"].str.lower() == name.lower()]
        if not recipe.empty:
            return recipe.iloc[0].to_dict()
        return None
    
    def add_recipe(self, recipe_data):
        """Add a new recipe and persist to CSV"""
        try:
            required_fields = ["Nosaukums", "Sastāvdaļas", "Pagatavošana"]
            if not all(field in recipe_data for field in required_fields):
                raise ValueError("Missing required fields")
            new_df = pd.DataFrame([recipe_data])
            self.df = pd.concat([self.df, new_df], ignore_index=True)
            self.save_to_csv()
            self.create_ingredient_chart(force=True)
            return True
        except Exception as e:
            logger.error(f"Error adding recipe: {e}")
            return False
    
    def create_ingredient_chart(self, force=False):
        """Generate a bar chart of ingredient frequency"""
        chart_path = Path("static/ingredients_chart.png")
        current_time = datetime.now()
        
        if (force or 
            not chart_path.exists() or 
            self.last_chart_update is None or 
            (current_time - self.last_chart_update).total_seconds() > app.config['CHART_UPDATE_INTERVAL']):
            
            try:
                ingredient_counts = self.df["Sastāvdaļas"].str.split(", ").explode().value_counts()
                if ingredient_counts.empty:
                    logger.warning("No ingredients to chart")
                    return False
                    
                plt.figure(figsize=(10, 6))
                ax = ingredient_counts.plot(kind='bar', color='skyblue')
                
                for i, v in enumerate(ingredient_counts):
                    ax.text(i, v + 0.1, str(v), ha='center', va='bottom')
                
                plt.xlabel("Ingredients", fontsize=12)
                plt.ylabel("Frequency", fontsize=12)
                plt.title("Most Popular Ingredients in Recipes", fontsize=14)
                plt.xticks(rotation=45, ha='right', fontsize=10)
                plt.grid(True, alpha=0.3)
                plt.tight_layout()
                
                os.makedirs("static", exist_ok=True)
                chart_path = Path(f"static/ingredients_chart_{int(current_time.timestamp())}.png")
                plt.savefig(chart_path, dpi=300, bbox_inches='tight')
                plt.close()
                
                for old_chart in Path("static").glob("ingredients_chart_*.png"):
                    if old_chart != chart_path:
                        old_chart.unlink()
                
                self.last_chart_update = current_time
                logger.info(f"Chart regenerated at {current_time}")
                return True
            except Exception as e:
                logger.error(f"Error generating chart: {e}")
                return False
        return True

# Initialize recipe manager
recipe_manager = RecipeManager(csv_file=app.config['CSV_FILE_PATH'])

# Check for required templates
def check_templates():
    required_templates = ['base.html', 'home.html', 'recipes.html', 'recipe_detail.html', 'about.html', 'search.html', '404.html', '500.html']
    missing_templates = []
    
    for template in required_templates:
        template_path = os.path.join(app.template_folder, template)
        if not os.path.exists(template_path):
            missing_templates.append(template)
    
    if missing_templates:
        logger.error(f"Missing templates: {missing_templates}")
        raise FileNotFoundError(f"Missing templates: {missing_templates}")

# Routes
@app.route('/')
def home():
    try:
        # Check if recipe_manager.df is properly initialized
        if recipe_manager.df is None or recipe_manager.df.empty:
            logger.warning("No recipes available in recipe_manager.df")
            return render_template(
                "home.html",
                recipe=None,
                recipes=[],
                recipe_count=0
            )

        featured_recipe = recipe_manager.get_recipe_by_index(0)
        all_recipes = recipe_manager.get_all_recipes()
        
        if not featured_recipe:
            logger.warning("No featured recipe found")
            return render_template(
                "home.html",
                recipe=None,
                recipes=all_recipes,
                recipe_count=len(recipe_manager.df)
            )

        return render_template(
            "home.html",
            recipe=featured_recipe,
            recipes=all_recipes,
            recipe_count=len(recipe_manager.df)
        )
    except Exception as e:
        logger.error(f"Error in home route: {e}", exc_info=True)
        abort(500)

@app.route('/recipes')
@cache.cached(timeout=300)
def recipes():
    try:
        all_recipes = recipe_manager.get_all_recipes()
        return render_template("recipes.html", recipes=all_recipes)
    except Exception as e:
        logger.error(f"Error in recipes route: {e}")
        abort(500)

@app.route('/recipe/<string:name>')
def recipe_detail(name):
    try:
        sanitized_name = bleach.clean(name)
        recipe = recipe_manager.get_recipe_by_name(sanitized_name)
        if recipe:
            return render_template("recipe_detail.html", recipe=recipe)
        abort(404)
    except Exception as e:
        logger.error(f"Error in recipe_detail route: {e}")
        abort(500)

@app.route('/about')
def about():
    facts = [
        "Vai zināji? Senajā Romā tomāti tika uzskatīti par indīgiem!",
        "Latviešu virtuve ir slavena ar rupjmaizi, pelēkajiem zirņiem un speķi.",
        "Pirmā pavārgrāmata tika publicēta 1746. gadā."
    ]
    return render_template("about.html", facts=facts)

@app.route('/search')
def search():
    try:
        query = bleach.clean(request.args.get('q', ''))
        if query:
            results = recipe_manager.df[
                recipe_manager.df['Nosaukums'].str.contains(query, case=False) |
                recipe_manager.df['Sastāvdaļas'].str.contains(query, case=False)
            ].to_dict(orient='records')
            return render_template('search.html', results=results, query=query)
        return render_template('search.html', results=[])
    except Exception as e:
        logger.error(f"Error in search route: {e}")
        abort(500)

@app.route('/api/recipes', methods=['GET', 'POST'])
@csrf.exempt  # Exempt API endpoint from CSRF protection (since it's stateless)
def api_recipes():
    if request.method == 'POST':
        try:
            recipe_data = request.get_json()
            if recipe_manager.add_recipe(recipe_data):
                return jsonify({'status': 'success', 'message': 'Recipe added'}), 201
            return jsonify({'status': 'error', 'message': 'Failed to add recipe'}), 400
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
    return jsonify(recipe_manager.get_all_recipes())

@app.route('/api/recipes/<string:name>')
def api_recipe_detail(name):
    sanitized_name = bleach.clean(name)
    recipe = recipe_manager.get_recipe_by_name(sanitized_name)
    if recipe:
        return jsonify(recipe)
    return jsonify({'error': 'Recipe not found'}), 404

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    try:
        return render_template('500.html'), 500
    except Exception as template_error:
        logger.error(f"Failed to render 500.html: {template_error}")
        return "500 - Server Error: Something went wrong on our end.", 500

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    logger.error(f"CSRF error: {e}")
    return jsonify({'status': 'error', 'message': 'CSRF token missing or invalid'}), 403

# Initialize the application
def initialize_app():
    os.makedirs("static", exist_ok=True)
    check_templates()  # Check for missing templates
    if not recipe_manager.create_ingredient_chart(force=True):
        logger.warning("Failed to generate ingredients chart during initialization")
    logger.info("Application initialized successfully")

if __name__ == '__main__':
    initialize_app()
    app.run(debug=Config.DEBUG)