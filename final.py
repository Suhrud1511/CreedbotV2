import os
import streamlit as st
import pymongo
from bson.objectid import ObjectId
import bcrypt
import logging
from datetime import datetime, time
from dotenv import load_dotenv
from datetime import datetime, time, date,timedelta
from typing import Tuple, Dict, Any, List, Optional
from pymongo.mongo_client import MongoClient
from pymongo.errors import (
    ServerSelectionTimeoutError,
    ConnectionFailure,
    OperationFailure
)

# Load environment variables and configure logging
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DatabaseManager:
    def __init__(self, uri: str, db_name: str, max_retries: int = 3, retry_delay: int = 2):
        """
        Initialize database connection with retry mechanism
        
        Args:
            uri: MongoDB connection URI
            db_name: Name of the database
            max_retries: Maximum number of connection attempts
            retry_delay: Delay between retries in seconds
        """
        self.uri = uri
        self.db_name = db_name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.client: Optional[MongoClient] = None
        self.db = None
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize connection
        self._connect_with_retry()

    def _connect_with_retry(self) -> None:
        """Establish database connection with retry mechanism"""
        for attempt in range(self.max_retries):
            try:
                # Configure MongoDB client with appropriate timeouts and settings
                self.client = pymongo.MongoClient(
                    self.uri,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    socketTimeoutMS=5000,
                    maxPoolSize=50,
                    retryWrites=True,
                    retryReads=True
                )
                
                # Test connection
                self.client.admin.command('ping')
                self.db = self.client[self.db_name]
                
                self.logger.info(f"Successfully connected to MongoDB database: {self.db_name}")
                self._ensure_indexes()
                return
                
            except ServerSelectionTimeoutError:
                self.logger.error(f"Server selection timeout - Attempt {attempt + 1}/{self.max_retries}")
                if attempt == self.max_retries - 1:
                    raise ConnectionError(
                        "Failed to connect to MongoDB. Please verify:\n"
                        "1. MongoDB server is running\n"
                        "2. Connection URI is correct\n"
                        "3. Network connectivity is available\n"
                        "4. Firewall settings allow connection"
                    )
                time.sleep(self.retry_delay)
                
            except ConnectionFailure as e:
                self.logger.error(f"Connection failure: {str(e)}")
                raise ConnectionError(f"Failed to connect to MongoDB: {str(e)}")
                
            except Exception as e:
                self.logger.error(f"Unexpected error during connection: {str(e)}")
                raise

    def _ensure_indexes(self) -> None:
        """Create necessary indexes for collections"""
        try:
            # Users collection indexes
            self.db.users.create_index([("email", 1)], unique=True)
            self.db.users.create_index([("phone", 1)], unique=True)
            
            # Rides collection indexes
            self.db.rides.create_index([("ride_id", 1)], unique=True)
            self.db.rides.create_index([("start_date", 1)])
            self.db.rides.create_index([("status", 1)])
            
            self.logger.info("Database indexes created successfully")
        except OperationFailure as e:
            self.logger.error(f"Failed to create indexes: {str(e)}")
            raise

    def get_collection(self, name: str):
        """Get a database collection"""
        if not self.db:
            raise ConnectionError("Database connection not established")
        return self.db[name]

    def ping(self) -> bool:
        """Test database connection"""
        try:
            self.client.admin.command('ping')
            return True
        except Exception as e:
            self.logger.error(f"Failed to ping database: {str(e)}")
            return False

    def close(self) -> None:
        """Close database connection"""
        if self.client:
            self.client.close()
            self.logger.info("Database connection closed")

    def __del__(self):
        """Cleanup on object destruction"""
        self.close()

    def insert_document(self, collection_name, document):
        return self.get_collection(collection_name).insert_one(document)

    def find_document(self, collection_name, query):
        return self.get_collection(collection_name).find_one(query)

    def update_document(self, collection_name, query, update):
        return self.get_collection(collection_name).update_one(query, {'$set': update})

    def get_next_ride_id(self):
        counter = self.get_collection("counters").find_one_and_update(
            {"_id": "ride_id"},
            {"$inc": {"seq": 1}},
            return_document=pymongo.ReturnDocument.AFTER
        )
        return counter['seq']

class UserManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.collection = "users"

    
    def _is_first_user(self):
        """Check if this is the first user being registered"""
        return self.db_manager.get_collection(self.collection).count_documents({}) == 0
    
    def create_user(self, name, phone, emergency_contact, email, password,
                is_existing_user=False, previous_stats=None, previous_rides=None):
      if not all([name, phone, emergency_contact, email, password]):
          raise ValueError("All fields are required")

      existing_user = self.db_manager.find_document(
          self.collection,
          {"$or": [{"phone": phone}, {"email": email}]}
      )
      if existing_user:
          raise ValueError("User with this phone or email already exists")

      hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
      
      # Initialize roles - first user gets admin
      roles = ["rider"]
      if self._is_first_user():
          roles.extend(["admin", "flag_holder"])

      # Create stats dictionary properly including total_rides
      stats = previous_stats or {}
      stats['total_rides'] = previous_rides or 0  # Explicitly set total_rides

      user_data = {
          "name": name,
          "phone": phone,
          "emergency_contact": emergency_contact,
          "email": email,
          "password": hashed_password,
          "roles": roles,
          "is_existing_user": is_existing_user,
          "stats": {
              "sweeps": stats.get('sweeps', 0),
              "leads": stats.get('leads', 0),
              "running_pilots": stats.get('running_pilots', 0),
              "ride_marshals": stats.get('ride_marshals', 0),
              "total_rides": stats.get('total_rides', 0)  # Include total_rides in stats
          },
          "created_at": datetime.utcnow()
      }

      result = self.db_manager.insert_document(self.collection, user_data)
      return result.inserted_id


    def authenticate_user(self, phone_or_email, password):
        user = self.db_manager.find_document(
            self.collection,
            {"$or": [{"phone": phone_or_email}, {"email": phone_or_email}]}
        )
        if user and bcrypt.checkpw(password.encode('utf-8'), user['password']):
            return user
        return None

    def update_user_role(self, user_id, new_role):
        user = self.db_manager.find_document(self.collection, {"_id": ObjectId(user_id)})
        if not user:
            raise ValueError("User not found")
        
        current_roles = user.get('roles', [])
        if new_role not in current_roles:
            current_roles.append(new_role)
            self.db_manager.update_document(
                self.collection,
                {"_id": ObjectId(user_id)},
                {"roles": current_roles}
            )
            return True
        return False
    def update_user_status(self, user_id: str, status: str) -> bool:
        """Update user status (active/blocked)"""
        try:
            self.db_manager.update_document(
                self.collection,
                {"_id": ObjectId(user_id)},
                {"status": status}
            )
            return True
        except Exception as e:
            logging.error(f"Error updating user status: {e}")
            return False

    def update_user_roles(self, user_id: str, roles: List[str]) -> bool:
        """Update user roles"""
        try:
            self.db_manager.update_document(
                self.collection,
                {"_id": ObjectId(user_id)},
                {"roles": roles}
            )
            return True
        except Exception as e:
            logging.error(f"Error updating user roles: {e}")
            return False

    def get_all_users(self) -> List[Dict]:
        """Get all users with their details"""
        return list(self.db_manager.get_collection(self.collection).find())


class RideManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.collection = "rides"
        self.MEETING_POINTS = [
            "Point A - North City",
            "Point B - South Plaza",
            "Point C - East Bridge",
            "Point D - West Gate"
        ]

    def _ensure_meeting_points(self):
        """Initialize meeting points collection if it doesn't exist"""
        if not self.db_manager.get_collection("meeting_points").find_one({}):
            default_points = [
                "Point A - North City",
                "Point B - South Plaza",
                "Point C - East Bridge",
                "Point D - West Gate"
            ]
            self.db_manager.get_collection("meeting_points").insert_many(
                [{"name": point} for point in default_points]
            )

    def get_meeting_points(self):
        """Get all meeting points"""
        return [doc["name"] for doc in self.db_manager.get_collection("meeting_points").find()]

    def add_meeting_point(self, point_name: str) -> bool:
        """Add a new meeting point"""
        try:
            if not self.db_manager.get_collection("meeting_points").find_one({"name": point_name}):
                self.db_manager.get_collection("meeting_points").insert_one({"name": point_name})
                return True
            return False
        except Exception as e:
            logging.error(f"Error adding meeting point: {e}")
            return False

    def remove_meeting_point(self, point_name: str) -> bool:
        """Remove a meeting point"""
        try:
            result = self.db_manager.get_collection("meeting_points").delete_one({"name": point_name})
            return result.deleted_count > 0
        except Exception as e:
            logging.error(f"Error removing meeting point: {e}")
            return False

    def _format_time(self, time_obj: time) -> str:
        """Convert time object to string format"""
        if isinstance(time_obj, time):
            return time_obj.strftime("%H:%M")
        return time_obj

    def _format_date(self, date_obj: datetime) -> datetime:
        """Convert date object to datetime format for MongoDB"""
        if isinstance(date_obj, datetime):
            return date_obj
        return datetime.combine(date_obj, time())

    def get_ride_by_id(self, ride_id: int) -> Optional[Dict]:
        """Retrieve a ride by its ID"""
        return self.db_manager.find_document(self.collection, {"ride_id": ride_id})

    def update_ride_status(self, ride_id: int, status: str) -> bool:
        """Update the status of a ride"""
        try:
            self.db_manager.update_document(
                self.collection,
                {"ride_id": ride_id},
                {"status": status}
            )
            return True
        except Exception as e:
            logging.error(f"Error updating ride status: {e}")
            return False

    def get_upcoming_rides(self) -> List[Dict]:
        """Get all upcoming rides"""
        current_date = datetime.now()
        return list(self.db_manager.get_collection(self.collection).find({
            "end_date": {"$gte": current_date}
        }).sort("start_date", 1))

    def get_past_rides(self) -> List[Dict]:
        """Get all past rides"""
        current_date = datetime.now()
        return list(self.db_manager.get_collection(self.collection).find({
            "end_date": {"$lt": current_date}
        }).sort("start_date", -1))

    def create_ride(self, name: str, meeting_point: str, meeting_time: time,
                   departure_time: time, arrival_time: time, 
                   start_date: datetime, end_date: datetime, 
                   description: str, creator_id: str) -> Tuple[str, str]:
        """
        Create a new ride with multi-day support
        Returns: Tuple of (ride_id, whatsapp_message)
        """
        ride_id = self.db_manager.get_next_ride_id()
        
        # Get creator details for ride marshal
        creator = self.db_manager.find_document("users", {"_id": ObjectId(creator_id)})
        
        # Initialize days list
        days = []
        current_date = start_date
        day_count = (end_date - start_date).days + 1
        
        for day in range(day_count):
            days.append({
                "day": day + 1,
                "date": current_date + timedelta(days=day),
                "roles": {
                    "lead": None,
                    "sweep": None,
                    "pilot": None,
                },
                "attendance": []
            })

        ride_data = {
            "ride_id": ride_id,
            "name": name,
            "meeting_point": meeting_point,
            "meeting_time": self._format_time(meeting_time),
            "departure_time": self._format_time(departure_time),
            "arrival_time": self._format_time(arrival_time),
            "start_date": self._format_date(start_date),
            "end_date": self._format_date(end_date),
            "description": description,
            "creator_id": creator_id,
            "ride_marshal": {
                "id": creator_id,
                "name": creator.get("name"),
                "phone": creator.get("phone")
            },
            "status": "pending",
            "days": days,
            "participants": [],
            "created_at": datetime.utcnow()
        }
        
        result = self.db_manager.insert_document(self.collection, ride_data)
        return result.inserted_id, self._generate_whatsapp_message(ride_data)

    def update_ride_day(self, ride_id: int, day_number: int, 
                       attendance: List[str], roles: Dict[str, str]) -> bool:
        """Update attendance and roles for a specific day of a ride"""
        try:
            ride = self.get_ride_by_id(ride_id)
            if not ride or day_number > len(ride['days']):
                return False

            ride['days'][day_number - 1]['attendance'] = attendance
            ride['days'][day_number - 1]['roles'] = roles

            self.db_manager.update_document(
                self.collection,
                {"ride_id": ride_id},
                {"days": ride['days']}
            )
            return True
        except Exception as e:
            logging.error(f"Error updating ride day: {e}")
            return False

    def add_participant(self, ride_id: int, user_id: str) -> bool:
        """Add a participant to a ride"""
        try:
            ride = self.get_ride_by_id(ride_id)
            if not ride:
                return False

            participants = ride.get('participants', [])
            if user_id not in participants:
                participants.append(user_id)
                self.db_manager.update_document(
                    self.collection,
                    {"ride_id": ride_id},
                    {"participants": participants}
                )
            return True
        except Exception as e:
            logging.error(f"Error adding participant: {e}")
            return False

    def remove_participant(self, ride_id: int, user_id: str) -> bool:
        """Remove a participant from a ride"""
        try:
            ride = self.get_ride_by_id(ride_id)
            if not ride:
                return False

            participants = ride.get('participants', [])
            if user_id in participants:
                participants.remove(user_id)
                self.db_manager.update_document(
                    self.collection,
                    {"ride_id": ride_id},
                    {"participants": participants}
                )
            return True
        except Exception as e:
            logging.error(f"Error removing participant: {e}")
            return False

    def _generate_whatsapp_message(self, ride_data: Dict[str, Any]) -> str:
        """Generate WhatsApp message format for ride details"""
        days_str = "1 day" if ride_data['start_date'] == ride_data['end_date'] else f"{(ride_data['end_date'] - ride_data['start_date']).days + 1} days"
        
        # Format dates for display
        start_date = ride_data['start_date'].strftime('%d-%b-%Y')
        end_date = ride_data['end_date'].strftime('%d-%b-%Y')
        
        message = f"""🏍️ *{ride_data['name']}*
        
📅 Date: {start_date}
{f"➡️ End Date: {end_date}" if start_date != end_date else ""}
⏰ Meeting Time: {ride_data['meeting_time']}
🚦 Departure Time: {ride_data['departure_time']}
📍 Meeting Point: {ride_data['meeting_point']}

📝 Description:
{ride_data['description']}

👮‍♂️ Ride Marshal: {ride_data['ride_marshal']['name']}
📱 Contact: {ride_data['ride_marshal']['phone']}

🎫 Ride ID: #{ride_data['ride_id']}
⏳ Duration: {days_str}

Please confirm your participation by responding in the group.
Remember to carry your gear and necessary documents.

#BikeLife #RideSafe"""
        
        return message

    def get_ride_statistics(self, ride_id: int) -> Dict[str, Any]:
        """Get comprehensive statistics for a ride"""
        ride = self.get_ride_by_id(ride_id)
        if not ride:
            return {}

        stats = {
            "total_participants": len(ride.get('participants', [])),
            "days": [],
            "total_attendance": 0
        }

        for day in ride.get('days', []):
            day_stats = {
                "day": day['day'],
                "date": day['date'],
                "attendance_count": len(day.get('attendance', [])),
                "roles": day.get('roles', {})
            }
            stats["days"].append(day_stats)
            stats["total_attendance"] += day_stats["attendance_count"]

        if stats["days"]:
            stats["average_attendance"] = stats["total_attendance"] / len(stats["days"])

        return stats

    def get_user_participation(self, user_id: str) -> Dict[str, Any]:
        """Get participation statistics for a specific user"""
        rides = list(self.db_manager.get_collection(self.collection).find({
            "$or": [
                {"participants": user_id},
                {"days.attendance": user_id},
                {"days.roles.lead": user_id},
                {"days.roles.sweep": user_id},
                {"days.roles.pilot": user_id}
            ]
        }))

        stats = {
            "total_rides_participated": 0,
            "total_days_attended": 0,
            "roles": {
                "lead": 0,
                "sweep": 0,
                "pilot": 0
            },
            "recent_rides": []
        }

        for ride in rides:
            stats["total_rides_participated"] += 1
            
            for day in ride.get('days', []):
                if user_id in day.get('attendance', []):
                    stats["total_days_attended"] += 1
                
                roles = day.get('roles', {})
                if roles.get('lead') == user_id:
                    stats["roles"]["lead"] += 1
                if roles.get('sweep') == user_id:
                    stats["roles"]["sweep"] += 1
                if roles.get('pilot') == user_id:
                    stats["roles"]["pilot"] += 1

            # Add to recent rides if within last 30 days
            if ride['end_date'] >= datetime.utcnow() - timedelta(days=30):
                stats["recent_rides"].append({
                    "ride_id": ride['ride_id'],
                    "name": ride['name'],
                    "date": ride['start_date']
                })

        return stats
class Dashboard:
    def __init__(self, user_manager, ride_manager):
        self.user_manager = user_manager
        self.ride_manager = ride_manager

    def _show_ride_history(self):
        st.markdown('<h1 class="section-header">Ride History</h1>', unsafe_allow_html=True)
        
        past_rides = self.ride_manager.get_past_rides()
        
        if not past_rides:
            st.info("No past rides found.")
            return
            
        for ride in past_rides:
            st.markdown(f"""
            <div class="ride-card">
                <h3>#{ride['ride_id']} - {ride['name']}</h3>
                <p><strong>Ride Marshal:</strong> {ride['ride_marshal']['name']}</p>
                <p>📍 {ride['meeting_point']}</p>
                <p>📅 {ride['start_date'].strftime('%Y-%m-%d')} | 
                   ⏰ {ride['meeting_time']} - {ride.get('arrival_time', 'N/A')}</p>
                <p>{ride.get('description', '')}</p>
            </div>
            """, unsafe_allow_html=True)

    def _show_meeting_point_management(self):
        st.markdown('<h1 class="section-header">Meeting Point Management</h1>', unsafe_allow_html=True)
        
        # Add new meeting point
        with st.form("add_meeting_point"):
            new_point = st.text_input("New Meeting Point")
            if st.form_submit_button("Add Meeting Point"):
                if new_point:
                    if self.ride_manager.add_meeting_point(new_point):
                        st.success("Meeting point added successfully!")
                        st.rerun()
                    else:
                        st.error("Failed to add meeting point")
        
        # List and remove existing points
        st.subheader("Existing Meeting Points")
        existing_points = self.ride_manager.get_meeting_points()
        
        for point in existing_points:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(point)
            with col2:
                if st.button("Remove", key=f"remove_{point}"):
                    if self.ride_manager.remove_meeting_point(point):
                        st.success("Meeting point removed successfully!")
                        st.rerun()
                    else:
                        st.error("Failed to remove meeting point")

    def show_dashboard(self, user):
        # Modern dark theme CSS
        st.markdown("""
            <style>
            /* Reset and base styles */
            :root {
                --bg-primary: #1a1b1e;
                --bg-secondary: #2c2e33;
                --text-primary: #ffffff;
                --text-secondary: #a0a0a0;
                --accent: #7c3aed;
                --accent-light: #9f67ff;
                --accent-dark: #6d28d9;
                --success: #10b981;
                --error: #ef4444;
                --warning: #f59e0b;
                --info: #3b82f6;
                --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.1);
                --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.1);
                --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1);
                --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            }

            /* Global Streamlit modifications */
            .stApp {
                background-color: var(--bg-primary);
                color: var(--text-primary);
            }

            /* Typography enhancements */
            h1, h2, h3, h4, h5, h6 {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                letter-spacing: -0.025em;
            }

            .section-header {
                color: var(--accent);
                font-size: 2rem;
                font-weight: 700;
                margin: 2rem 0 1.5rem;
                padding-bottom: 0.75rem;
                border-bottom: 2px solid var(--accent);
                background: linear-gradient(90deg, var(--accent) 0%, transparent 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }

            /* Card Components */
            .stat-card {
                background: linear-gradient(145deg, var(--bg-secondary), var(--bg-primary));
                border-radius: 1rem;
                padding: 1.5rem;
                margin: 1rem 0;
                box-shadow: var(--shadow-md);
                border: 1px solid rgba(124, 58, 237, 0.1);
                transition: var(--transition);
                backdrop-filter: blur(10px);
                position: relative;
                overflow: hidden;
            }

            .stat-card::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: linear-gradient(45deg, transparent, rgba(124, 58, 237, 0.1));
                opacity: 0;
                transition: var(--transition);
            }

            .stat-card:hover {
                transform: translateY(-4px);
                border-color: var(--accent);
                box-shadow: var(--shadow-lg);
            }

            .stat-card:hover::before {
                opacity: 1;
            }

            .stat-card h3 {
                color: var(--text-secondary);
                font-size: 0.875rem;
                font-weight: 500;
                margin-bottom: 0.5rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }

            .stat-card h2 {
                color: var(--text-primary);
                font-size: 2rem;
                font-weight: 700;
                margin: 0;
                background: linear-gradient(90deg, var(--accent) 0%, var(--accent-light) 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }

            /* Ride Card Component */
            .ride-card {
                background: linear-gradient(145deg, var(--bg-secondary), var(--bg-primary));
                border-radius: 1rem;
                padding: 2rem;
                margin: 1.5rem 0;
                box-shadow: var(--shadow-md);
                border: 1px solid rgba(124, 58, 237, 0.1);
                transition: var(--transition);
                position: relative;
                overflow: hidden;
            }

            .ride-card:hover {
                transform: translateY(-4px);
                border-color: var(--accent);
                box-shadow: var(--shadow-lg);
            }

            .ride-card h3 {
                font-size: 1.5rem;
                font-weight: 700;
                margin-bottom: 1rem;
                color: var(--text-primary);
            }

            .ride-card p {
                color: var(--text-secondary);
                margin: 0.5rem 0;
                line-height: 1.6;
            }

            /* Form Elements */
            .stTextInput input, 
            .stSelectbox select, 
            .stDateInput input,
            .stTimeInput input {
                background-color: var(--bg-secondary) !important;
                color: var(--text-primary) !important;
                border: 1px solid rgba(124, 58, 237, 0.2) !important;
                border-radius: 0.5rem !important;
                padding: 0.75rem 1rem !important;
                transition: var(--transition) !important;
            }

            .stTextInput input:focus, 
            .stSelectbox select:focus,
            .stDateInput input:focus,
            .stTimeInput input:focus {
                border-color: var(--accent) !important;
                box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.2) !important;
                outline: none !important;
            }

            /* Button Styles */
            .stButton button {
                background: linear-gradient(45deg, var(--accent-dark), var(--accent)) !important;
                color: white !important;
                border: none !important;
                border-radius: 0.5rem !important;
                padding: 0.75rem 1.5rem !important;
                font-weight: 600 !important;
                letter-spacing: 0.025em !important;
                transition: var(--transition) !important;
                text-transform: uppercase !important;
                box-shadow: var(--shadow-sm) !important;
            }

            .stButton button:hover {
                background: linear-gradient(45deg, var(--accent), var(--accent-light)) !important;
                transform: translateY(-2px) !important;
                box-shadow: var(--shadow-md) !important;
            }

            /* Alert/Message Styles */
            .stSuccess, .stInfo, .stWarning, .stError {
                padding: 1rem !important;
                border-radius: 0.5rem !important;
                margin: 1rem 0 !important;
                animation: slideIn 0.3s ease-out;
            }

            .stSuccess {
                background-color: rgba(16, 185, 129, 0.1) !important;
                border: 1px solid var(--success) !important;
            }

            .stError {
                background-color: rgba(239, 68, 68, 0.1) !important;
                border: 1px solid var(--error) !important;
            }

            /* Sidebar Enhancements */
            .css-1d391kg {
                background-color: var(--bg-secondary) !important;
            }

            /* Animations */
            @keyframes slideIn {
                from {
                    transform: translateY(-10px);
                    opacity: 0;
                }
                to {
                    transform: translateY(0);
                    opacity: 1;
                }
            }

            /* Responsive Design */
            @media (max-width: 768px) {
                .section-header {
                    font-size: 1.5rem;
                }
                
                .stat-card {
                    padding: 1rem;
                }
                
                .stat-card h2 {
                    font-size: 1.5rem;
                }
                
                .ride-card {
                    padding: 1.5rem;
                }
                
                .ride-card h3 {
                    font-size: 1.25rem;
                }
            }

            /* Custom Scrollbar */
            ::-webkit-scrollbar {
                width: 8px;
                height: 8px;
            }

            ::-webkit-scrollbar-track {
                background: var(--bg-primary);
            }

            ::-webkit-scrollbar-thumb {
                background: var(--accent);
                border-radius: 4px;
            }

            ::-webkit-scrollbar-thumb:hover {
                background: var(--accent-light);
            }
            </style>
        """, unsafe_allow_html=True)

        available_pages = ["Dashboard", "Ride History"]  # Add Ride History to all roles
        if "flag_holder" in user['roles']:
            available_pages.extend(["Create Ride", "Pre-ride Report"])
        if "admin" in user['roles']:
            available_pages.extend(["User Management", "Meeting Point Management"])  # Add new admin page
        if "admin" in user['roles'] or "flag_holder" in user['roles']:
            available_pages.extend(["Attendance"])

        icons = {
            "Dashboard": "📊",
            "Create Ride": "🏍️",
            "User Management": "👥",
            "Attendance": "✓",
            "Pre-ride Report": "📋",
            "Ride History": "📜",
            "Meeting Point Management": "📍"
        }

        selected_page = st.sidebar.radio(
            "",
            available_pages,
            format_func=lambda x: f"{icons.get(x, '')} {x}"
        )

        if selected_page == "Dashboard":
            self._show_main_dashboard(user)
        elif selected_page == "Create Ride":
            self._show_ride_creation(user)
        elif selected_page == "User Management":
            self._show_user_management()
        elif selected_page == "Attendance":
            self._show_attendance_marking()
        elif selected_page == "Pre-ride Report":
            self._show_preride_report()
        elif selected_page == "Ride History":
            self._show_ride_history()
        elif selected_page == "Meeting Point Management":
            self._show_meeting_point_management()

    def _calculate_total_rides(self, user_id):
      """
      Calculate total rides by:
      1. For non-existing users: Only count current system participation
      2. For existing users: Add previous system rides to current participation
      """
      # Get user details
      user = self.user_manager.db_manager.find_document("users", {"_id": user_id})
      
      # Get current system participation
      participation_stats = self.ride_manager.get_user_participation(str(user_id))
      current_rides = participation_stats.get('total_days_attended', 0)
      
      # If user is from previous system, add their previous rides
      if user.get('is_existing_user', False):
          previous_rides = user.get('stats', {}).get('total_rides', 0)
          total_rides = previous_rides + current_rides
      else:
          # For new users, only count current system rides
          total_rides = current_rides
      
      return total_rides

    def _show_rider_stats(self, user):
        """Show rider statistics including previous and current rides"""
        total_rides = self._calculate_total_rides(user['_id'])
        
        # Get current participation stats
        participation = self.ride_manager.get_user_participation(str(user['_id']))
        
        # Get previous stats
        stats = user.get('stats', {})
        
        # For existing users, only add current participation to previous stats
        # For new users, just show current participation
        if user.get('is_existing_user', False):
            combined_stats = {
                'total_rides': total_rides,  # This now correctly includes both previous and current rides
                'leads': stats.get('leads', 0) + participation['roles']['lead'],
                'sweeps': stats.get('sweeps', 0) + participation['roles']['sweep'],
                'running_pilots': stats.get('running_pilots', 0) + participation['roles']['pilot'],
                'ride_marshals': stats.get('ride_marshals', 0)
            }
        else:
            combined_stats = {
                'total_rides': participation['total_days_attended'],
                'leads': participation['roles']['lead'],
                'sweeps': participation['roles']['sweep'],
                'running_pilots': participation['roles']['pilot'],
                'ride_marshals': 0
            }
        
        col1, col2, col3, col4, col5 = st.columns(5)
        stats_data = [
            ("🛣️ Total Rides", combined_stats['total_rides']),
            ("🚦 Lead", combined_stats['leads']),
            ("🔧 Sweep", combined_stats['sweeps']),
            ("🏃 Running Pilot", combined_stats['running_pilots']),
            ("👮 Marshal", combined_stats['ride_marshals'])
        ]
        
        for col, (label, value) in zip([col1, col2, col3, col4, col5], stats_data):
            with col:
                st.markdown(f"""
                <div class="stat-card">
                    <h3>{label}</h3>
                    <h2>{value}</h2>
                </div>
                """, unsafe_allow_html=True)

    def _show_main_dashboard(self, user):
        st.markdown('<h1 class="section-header">🏍️ Dashboard</h1>', unsafe_allow_html=True)
        self._show_rider_stats(user)
        st.markdown('<h2 class="section-header">Available Rides</h2>', unsafe_allow_html=True)
        self._show_available_rides(user)

    def _show_ride_creation(self, user):
        st.markdown('<h1 class="section-header">Create New Ride</h1>', unsafe_allow_html=True)
        
        with st.form("create_ride", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Ride Name")
                meeting_point = st.selectbox("Meeting Point", self.ride_manager.MEETING_POINTS)
                start_date = st.date_input("Start Date")
                end_date = st.date_input("End Date")
            
            with col2:
                meeting_time = st.time_input("Meeting Time")
                departure_time = st.time_input("Departure Time")
                arrival_time = st.time_input("Expected Arrival Time")

            description = st.text_area("Description")
            
            if st.form_submit_button("Create Ride"):
                if not all([name, meeting_point, start_date, end_date, description]):
                    st.error("All fields are required!")
                else:
                    try:
                        ride_id, whatsapp_msg = self.ride_manager.create_ride(
                            name=name,
                            meeting_point=meeting_point,
                            meeting_time=meeting_time,
                            departure_time=departure_time,
                            arrival_time=arrival_time,
                            start_date=datetime.combine(start_date, time()),
                            end_date=datetime.combine(end_date, time()),
                            description=description,
                            creator_id=str(user['_id'])
                        )
                        st.success("Ride created successfully!")
                        
                        # Show WhatsApp message in expandable section
                        with st.expander("WhatsApp Message Format"):
                            st.code(whatsapp_msg, language=None)
                            
                    except Exception as e:
                        st.error(f"Failed to create ride: {str(e)}")

    def _show_available_rides(self, user):
        # Get upcoming rides using RideManager
        upcoming_rides = self.ride_manager.get_upcoming_rides()
        
        for ride in upcoming_rides:
            # Format dates for display
            start_date = ride['start_date'].strftime('%Y-%m-%d')
            end_date = ride['end_date'].strftime('%Y-%m-%d')
            date_display = start_date if start_date == end_date else f"{start_date} to {end_date}"
            
            st.markdown(f"""
            <div class="ride-card">
                <h3>#{ride['ride_id']} - {ride['name']}</h3>
                <p>📍 {ride['meeting_point']}</p>
                <p>📅 {date_display} | ⏰ {ride['meeting_time']}</p>
                <p>{ride.get('description', '')}</p>
            </div>
            """, unsafe_allow_html=True)
            
            col1, col2 = st.columns([1, 4])
            with col1:
                str_user_id = str(user['_id'])
                if str_user_id in [str(p) for p in ride.get('participants', [])]:
                    if st.button("Leave", key=f"leave_{ride['ride_id']}"):
                        if self.ride_manager.remove_participant(ride['ride_id'], str_user_id):
                            st.rerun()
                else:
                    if st.button("Join", key=f"join_{ride['ride_id']}"):
                        if self.ride_manager.add_participant(ride['ride_id'], str_user_id):
                            st.rerun()

    def _show_attendance_marking(self):
        st.markdown('<h1 class="section-header">Mark Attendance</h1>', unsafe_allow_html=True)
        
        # Get rides sorted by date
        rides = self.ride_manager.get_upcoming_rides()
        past_rides = self.ride_manager.get_past_rides()
        all_rides = rides + past_rides
        
        for ride in all_rides:
            ride_dates = f"{ride['start_date'].strftime('%Y-%m-%d')} to {ride['end_date'].strftime('%Y-%m-%d')}"
            with st.expander(f"#{ride['ride_id']} - {ride['name']} ({ride_dates})"):
                users = list(self.user_manager.db_manager.get_collection("users").find())
                
                # Show attendance for each day
                for day in ride['days']:
                    st.subheader(f"Day {day['day']} - {day['date'].strftime('%Y-%m-%d')}")
                    
                    # Current attendance and roles
                    current_attendance = day.get('attendance', [])
                    current_roles = day.get('roles', {})
                    
                    # Convert all IDs to strings for consistent comparison
                    current_attendance = [str(id) for id in current_attendance]
                    
                    selected_users = st.multiselect(
                        f"Select present riders for Day {day['day']}",
                        options=[str(user['_id']) for user in users],
                        default=current_attendance,
                        format_func=lambda x: next(
                            (user['name'] for user in users if str(user['_id']) == x),
                            str(x)
                        ),
                        key=f"attendance_{ride['ride_id']}_{day['day']}"
                    )
                    
                    # Roles selection
                    col1, col2 = st.columns(2)
                    with col1:
                        lead = st.selectbox(
                            "Lead Rider",
                            options=[str(user['_id']) for user in users],
                            format_func=lambda x: next(
                                (user['name'] for user in users if str(user['_id']) == x),
                                str(x)
                            ),
                            key=f"lead_{ride['ride_id']}_{day['day']}"
                        )
                        
                        sweep = st.selectbox(
                            "Sweep Rider",
                            options=[str(user['_id']) for user in users],
                            format_func=lambda x: next(
                                (user['name'] for user in users if str(user['_id']) == x),
                                str(x)
                            ),
                            key=f"sweep_{ride['ride_id']}_{day['day']}"
                        )
                    
                    with col2:
                        pilot = st.selectbox(
                            "Running Pilot",
                            options=[str(user['_id']) for user in users],
                            format_func=lambda x: next(
                                (user['name'] for user in users if str(user['_id']) == x),
                                str(x)
                            ),
                            key=f"pilot_{ride['ride_id']}_{day['day']}"
                        )
                    
                    if st.button(f"Update Day {day['day']}", key=f"update_day_{ride['ride_id']}_{day['day']}"):
                        roles = {
                            "lead": lead,
                            "sweep": sweep,
                            "pilot": pilot
                        }
                        if self.ride_manager.update_ride_day(ride['ride_id'], day['day'], selected_users, roles):
                            st.success(f"Day {day['day']} updated successfully!")
                            st.rerun()
                        else:
                            st.error("Failed to update attendance and roles")

    def _show_user_management(self):
      st.markdown('<h1 class="section-header">User Management</h1>', unsafe_allow_html=True)
      
      users = self.user_manager.get_all_users()
      
      # Filter controls
      col1, col2 = st.columns(2)
      with col1:
          status_filter = st.selectbox(
              "Filter by Status",
              ["All", "Active", "Blocked"],
              key="status_filter"
          )
      with col2:
          role_filter = st.selectbox(
              "Filter by Role",
              ["All", "Admin", "Flag Holder", "Rider"],
              key="role_filter"
          )
      
      for user in users:
          # Apply filters
          if status_filter != "All" and user.get('status', 'Active') != status_filter:
              continue
          if role_filter != "All" and role_filter.lower().replace(" ", "_") not in user.get('roles', []):
              continue

          with st.expander(f"{user['name']} ({user['email']})"):
              col1, col2 = st.columns(2)
              
              with col1:
                  st.write(f"📱 Phone: {user['phone']}")
                  st.write(f"🚨 Emergency Contact: {user['emergency_contact']}")
                  st.write(f"🎭 Roles: {', '.join(user['roles'])}")
                  st.write(f"📅 Joined: {user['created_at'].strftime('%Y-%m-%d')}")
              
              with col2:
                  # Role management buttons
                  if "admin" not in user['roles']:
                      if st.button("Make Admin", key=f"admin_{user['_id']}"):
                          roles = user.get('roles', []) + ['admin']
                          if self.user_manager.update_user_roles(str(user['_id']), roles):
                              st.success("User promoted to Admin")
                              st.rerun()

                  if "flag_holder" not in user['roles']:
                      if st.button("Make Flag Holder", key=f"fh_{user['_id']}"):
                          roles = user.get('roles', []) + ['flag_holder']
                          if self.user_manager.update_user_roles(str(user['_id']), roles):
                              st.success("User promoted to Flag Holder")
                              st.rerun()

                  # Status toggle
                  current_status = user.get('status', 'Active')
                  if st.button(
                      "Block User" if current_status == 'Active' else "Unblock User",
                      key=f"status_{user['_id']}"
                  ):
                      new_status = 'Blocked' if current_status == 'Active' else 'Active'
                      if self.user_manager.update_user_status(str(user['_id']), new_status):
                          st.success(f"User {new_status.lower()}")
                          st.rerun()
    def _get_eligibility_status(self, user_id):
        """Determine rider eligibility based on combined previous and current stats"""
        user = self.user_manager.db_manager.find_document("users", {"_id": user_id})
        stats = user.get('stats', {})
        
        # Get current participation
        participation = self.ride_manager.get_user_participation(str(user_id))
        
        # Combine previous and current stats
        total_rides = self._calculate_total_rides(user_id)
        total_sweeps = stats.get('sweeps', 0) + participation['roles']['sweep']
        total_leads = stats.get('leads', 0) + participation['roles']['lead']
        
        return {
            'sweep_eligible': total_rides >= 10,
            'lead_eligible': total_sweeps >= 3,
            'rp_eligible': total_sweeps >= 3 and total_leads >= 3
        }
    def _show_preride_report(self):
        """Display pre-ride report with user eligibility based on combined stats"""
        st.markdown('<h1 class="section-header">Pre-ride Report</h1>', unsafe_allow_html=True)
        
        # Display eligibility rules
        with st.expander("📋 Eligibility Rules", expanded=True):
            st.markdown("""
            <div style='background-color: var(--bg-secondary); padding: 20px; border-radius: 10px;'>
                <h3>Role Eligibility Criteria:</h3>
                <ul>
                    <li>🟢 <strong>Sweep Role:</strong> Minimum 10 rides required</li>
                    <li>🔵 <strong>Lead Role:</strong> Minimum 3 sweeps required</li>
                    <li>🟡 <strong>Running Pilot (RP):</strong> Minimum 3 sweeps AND 3 leads required</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

        # Get all users
        users = list(self.user_manager.db_manager.get_collection("users").find())
        
        for user in users:
            stats = user.get('stats', {})
            participation = self.ride_manager.get_user_participation(str(user['_id']))
            eligibility = self._get_eligibility_status(user['_id'])
            
            # Calculate combined stats
            combined_stats = {
                'total_rides': self._calculate_total_rides(user['_id']),
                'sweeps': stats.get('sweeps', 0) + participation['roles']['sweep'],
                'leads': stats.get('leads', 0) + participation['roles']['lead'],
                'running_pilots': stats.get('running_pilots', 0) + participation['roles']['pilot']
            }
            
            # Determine card color based on highest eligible role
            card_color = "var(--bg-secondary)"  # default color
            if eligibility['rp_eligible']:
                card_color = "rgba(234, 179, 8, 0.2)"  # yellow tint
            elif eligibility['lead_eligible']:
                card_color = "rgba(59, 130, 246, 0.2)"  # blue tint
            elif eligibility['sweep_eligible']:
                card_color = "rgba(34, 197, 94, 0.2)"  # green tint

            st.markdown(f"""
            <div style='
                background-color: {card_color};
                padding: 20px;
                border-radius: 10px;
                margin-bottom: 10px;
            '>
                <h3>{user['name']}</h3>
                <div style='display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px;'>
                    <div>
                        <p><strong>Total Rides:</strong> {combined_stats['total_rides']}</p>
                        <p><strong>Total Sweeps:</strong> {combined_stats['sweeps']}</p>
                    </div>
                    <div>
                        <p><strong>Total Leads:</strong> {combined_stats['leads']}</p>
                        <p><strong>Running Pilot Days:</strong> {combined_stats['running_pilots']}</p>
                    </div>
                </div>
                <div style='margin-top: 10px;'>
                    <p><strong>Eligible for:</strong></p>
                    <p>
                        {' 🟢 Sweep ' if eligibility['sweep_eligible'] else '❌ Sweep '}
                        {' 🔵 Lead ' if eligibility['lead_eligible'] else '❌ Lead '}
                        {' 🟡 Running Pilot ' if eligibility['rp_eligible'] else '❌ Running Pilot '}
                    </p>
                </div>
            </div>
            """, unsafe_allow_html=True)
            # Add emergency contact in expandable section
            with st.expander("View Emergency Contact"):
                st.write(f"📞 Emergency Contact: {user['emergency_contact']}")    
def main():
    st.set_page_config(page_title="Bikers Club", page_icon="🏍️", layout="wide")
    
    # Initialize managers
    db_manager = DatabaseManager(
        uri=os.getenv("MONGO_URI", "mongodb://localhost:27017/"),
        db_name="bikers_club"
    )
    user_manager = UserManager(db_manager)
    ride_manager = RideManager(db_manager)
    dashboard = Dashboard(user_manager, ride_manager)

    # Initialize session states
    if 'user' not in st.session_state:
        st.session_state.user = None
    if 'is_existing' not in st.session_state:
        st.session_state.is_existing = False

    # Logout functionality
    if st.session_state.user and st.sidebar.button("Logout"):
        st.session_state.user = None
        st.session_state.is_existing = False
        st.rerun()

    if st.session_state.user is None:
        st.title("🏍️ Bikers Club")
        
        tab1, tab2 = st.tabs(["Login", "Register"])
        
        with tab1:
            with st.form("login"):
                phone_or_email = st.text_input("Phone or Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Login"):
                    user = user_manager.authenticate_user(phone_or_email, password)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        st.error("Invalid credentials")
        
        with tab2:
            # Basic Information
            st.subheader("Basic Information")
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Name")
                phone = st.text_input("Phone")
                email = st.text_input("Email")
            with col2:
                emergency_contact = st.text_input("Emergency Contact")
                password = st.text_input("Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
            
            # Previous System User Information
            is_existing = st.checkbox("Existing user from previous system?", 
                                   value=st.session_state.is_existing,
                                   key="existing_user_checkbox")
            
            # Update session state
            st.session_state.is_existing = is_existing
            
            # Initialize variables
            previous_stats = None
            previous_rides = None
            
            # Show previous user fields if checkbox is checked
            if is_existing:
                st.subheader("Previous Riding History")
                col1, col2 = st.columns(2)
                with col1:
                    previous_rides = st.number_input("Previous Rides", 
                                                   min_value=0, 
                                                   value=0)
                    sweeps = st.number_input("Previous Sweeps", 
                                           min_value=0, 
                                           value=0)
                    leads = st.number_input("Previous Leads", 
                                          min_value=0, 
                                          value=0)
                with col2:
                    running_pilots = st.number_input("Previous Running Pilots", 
                                                   min_value=0, 
                                                   value=0)
                    ride_marshals = st.number_input("Previous Ride Marshals", 
                                                  min_value=0, 
                                                  value=0)
                
                previous_stats = {
                    "sweeps": sweeps,
                    "leads": leads,
                    "running_pilots": running_pilots,
                    "ride_marshals": ride_marshals
                }
            
            # Registration button outside the columns
            if st.button("Register"):
                try:
                    # Validation
                    if not all([name, phone, emergency_contact, email, password]):
                        st.error("All fields are required")
                    elif password != confirm_password:
                        st.error("Passwords do not match")
                    else:
                        # Create user
                        user_id = user_manager.create_user(
                            name=name,
                            phone=phone,
                            emergency_contact=emergency_contact,
                            email=email,
                            password=password,
                            is_existing_user=is_existing,
                            previous_stats=previous_stats,
                            previous_rides=previous_rides
                        )
                        
                        success_message = "Registration successful! Please login."
                        if user_manager._is_first_user():
                            success_message += " You have been granted admin privileges as the first user."
                        st.success(success_message)
                        
                        # Reset the existing user state
                        st.session_state.is_existing = False
                        st.rerun()
                except ValueError as e:
                    st.error(str(e))

    else:
        # Display user info in sidebar
        with st.sidebar:
            st.write(f"Welcome, {st.session_state.user['name']}")
            st.write("Roles:", ", ".join(st.session_state.user['roles']))
            st.divider()
        
        # Show dashboard based on user roles
        dashboard.show_dashboard(st.session_state.user)

if __name__ == "__main__":
    main()