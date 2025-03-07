import os
import streamlit as st
import pymongo
from bson.objectid import ObjectId
import bcrypt
import logging
from datetime import datetime, time
from dotenv import load_dotenv
from datetime import datetime, time, date, timedelta
from typing import Tuple, Dict, Any, List, Optional
import urllib.parse
import functools

# Load environment variables and configure logging
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class DatabaseManager:
    def __init__(self, uri, db_name):
        try:
            self.client = pymongo.MongoClient(uri)
            self.db = self.client[db_name]
            self._ensure_ride_counter()
            logging.info("Connected to MongoDB")
        except Exception as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            raise

    def _ensure_ride_counter(self):
        if not self.db.counters.find_one({"_id": "ride_id"}):
            self.db.counters.insert_one({"_id": "ride_id", "seq": 200})

    def get_collection(self, name):
        return self.db[name]

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

    @st.cache_data(ttl=300)  # Cache for 5 minutes
    def get_all_users(_self) -> List[Dict]:
        """Get all users with their details"""
        return list(_self.db_manager.get_collection(_self.collection).find())
    
    @st.cache_data(ttl=300)  # Cache for 5 minutes
    def get_registered_users_for_ride(_self, ride_id: int) -> List[Dict]:
        """Get all users registered for a specific ride"""
        ride = _self.db_manager.find_document("rides", {"ride_id": ride_id})
        if not ride:
            return []
            
        participant_ids = ride.get('participants', [])
        participant_objs = []
        
        # Convert string IDs to ObjectId if needed
        obj_ids = []
        for user_id in participant_ids:
            try:
                if isinstance(user_id, str):
                    obj_ids.append(ObjectId(user_id))
                else:
                    obj_ids.append(user_id)
            except Exception as e:
                logging.error(f"Error converting user ID: {e}")
                
        if obj_ids:
            participant_objs = list(_self.db_manager.get_collection(_self.collection).find(
                {"_id": {"$in": obj_ids}}
            ))
            
        return participant_objs


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

    @st.cache_data(ttl=300)  # Cache for 5 minutes
    def get_meeting_points(_self):
        """Get all meeting points"""
        return [doc["name"] for doc in _self.db_manager.get_collection("meeting_points").find()]

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

    @st.cache_data(ttl=60)  # Cache for 1 minute
    def get_ride_by_id(_self, ride_id: int) -> Optional[Dict]:
        """Retrieve a ride by its ID"""
        return _self.db_manager.find_document(_self.collection, {"ride_id": ride_id})

    def update_ride_status(self, ride_id: int, status: str) -> bool:
        """Update the status of a ride"""
        try:
            self.db_manager.update_document(
                self.collection,
                {"ride_id": ride_id},
                {"status": status}
            )
            # Clear cache when updating ride status
            self.get_ride_by_id.clear()
            self.get_upcoming_rides.clear()
            self.get_past_rides.clear()
            return True
        except Exception as e:
            logging.error(f"Error updating ride status: {e}")
            return False

    @st.cache_data(ttl=60)  # Cache for 1 minute
    def get_upcoming_rides(_self) -> List[Dict]:
        """Get all upcoming rides"""
        current_date = datetime.now()
        return list(_self.db_manager.get_collection(_self.collection).find({
            "end_date": {"$gte": current_date}
        }).sort("start_date", 1))

    @st.cache_data(ttl=300)  # Cache for 5 minutes
    def get_past_rides(_self) -> List[Dict]:
        """Get all past rides"""
        current_date = datetime.now()
        return list(_self.db_manager.get_collection(_self.collection).find({
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
                    "pilot2": None  # Adding second running pilot role
                },
                "has_second_pilot": False,  # Flag to indicate if a second pilot is needed
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
        
        # Clear caches when creating a new ride
        self.get_upcoming_rides.clear()
        
        return result.inserted_id, self._generate_whatsapp_message(ride_data)

    def update_ride_day(self, ride_id: int, day_number: int, 
                       attendance: List[str], roles: Dict[str, str],
                       has_second_pilot: bool) -> bool:
        """Update attendance and roles for a specific day of a ride"""
        try:
            ride = self.get_ride_by_id(ride_id)
            if not ride or day_number > len(ride['days']):
                return False

            ride['days'][day_number - 1]['attendance'] = attendance
            ride['days'][day_number - 1]['roles'] = roles
            ride['days'][day_number - 1]['has_second_pilot'] = has_second_pilot

            self.db_manager.update_document(
                self.collection,
                {"ride_id": ride_id},
                {"days": ride['days']}
            )
            
            # Clear caches that might contain this ride's data
            self.get_ride_by_id.clear()
            
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
                
                # Clear caches that might contain this ride's data
                self.get_ride_by_id.clear()
                
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
                
                # Clear caches that might contain this ride's data
                self.get_ride_by_id.clear()
                
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

    @st.cache_data(ttl=60)  # Cache for 1 minute
    def get_ride_statistics(_self, ride_id: int) -> Dict[str, Any]:
        """Get comprehensive statistics for a ride"""
        ride = _self.get_ride_by_id(ride_id)
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
                "roles": day.get('roles', {}),
                "has_second_pilot": day.get('has_second_pilot', False)
            }
            stats["days"].append(day_stats)
            stats["total_attendance"] += day_stats["attendance_count"]

        if stats["days"]:
            stats["average_attendance"] = stats["total_attendance"] / len(stats["days"])

        return stats

    @st.cache_data(ttl=60)  # Cache for 1 minute
    def get_user_participation(_self, user_id: str) -> Dict[str, Any]:
        """Get participation statistics for a specific user"""
        rides = list(_self.db_manager.get_collection(_self.collection).find({
            "$or": [
                {"participants": user_id},
                {"days.attendance": user_id},
                {"days.roles.lead": user_id},
                {"days.roles.sweep": user_id},
                {"days.roles.pilot": user_id},
                {"days.roles.pilot2": user_id}  # Include second pilot role
            ]
        }))

        stats = {
            "total_rides_participated": 0,
            "total_days_attended": 0,
            "roles": {
                "lead": 0,
                "sweep": 0,
                "pilot": 0,
                "pilot2": 0  # Add second pilot count
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
                if roles.get('pilot2') == user_id:
                    stats["roles"]["pilot2"] += 1

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
            
            /* Eligibility Badges */
            .eligibility-badge {
                display: inline-block;
                padding: 4px 8px;
                margin-right: 8px;
                border-radius: 4px;
                font-size: 0.8rem;
                font-weight: 600;
            }
            
            .eligible {
                background-color: rgba(16, 185, 129, 0.2);
                color: #10b981;
                border: 1px solid #10b981;
            }
            
            .not-eligible {
                background-color: rgba(239, 68, 68, 0.2);
                color: #ef4444;
                border: 1px solid #ef4444;
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

    @st.cache_data(ttl=60)  # Cache for 1 minute
    def _calculate_total_rides(_self, _user_id):
        """
        Calculate total rides by:
        1. For non-existing users: Only count current system participation
        2. For existing users: Add previous system rides to current participation
        """
        # Convert ObjectId to string if it's not already a string
        user_id_str = str(_user_id)
        user_id = _user_id  # Keep original for MongoDB query
        
        # Get user details
        user = _self.user_manager.db_manager.find_document("users", {"_id": user_id})
        
        # Get current system participation
        participation_stats = _self.ride_manager.get_user_participation(user_id_str)
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
                'running_pilots': stats.get('running_pilots', 0) + participation['roles']['pilot'] + participation['roles']['pilot2'],
                'ride_marshals': stats.get('ride_marshals', 0)
            }
        else:
            combined_stats = {
                'total_rides': participation['total_days_attended'],
                'leads': participation['roles']['lead'],
                'sweeps': participation['roles']['sweep'],
                'running_pilots': participation['roles']['pilot'] + participation['roles']['pilot2'],
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
        
        if not upcoming_rides:
            st.info("No upcoming rides available to join.")
            return
            
        for ride in upcoming_rides:
            # Format dates for display
            start_date = ride['start_date'].strftime('%Y-%m-%d')
            end_date = ride['end_date'].strftime('%Y-%m-%d')
            date_display = start_date if start_date == end_date else f"{start_date} to {end_date}"
            
            # Check if user is a participant
            str_user_id = str(user['_id'])
            is_registered = str_user_id in [str(p) for p in ride.get('participants', [])]
            
            # Add status badge to the ride card
            status_badge = ""
            if is_registered:
                status_badge = """<span style="background-color: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; float: right;">Joined ✓</span>"""
            
            st.markdown(f"""
            <div class="ride-card">
                <h3>#{ride['ride_id']} - {ride['name']} {status_badge}</h3>
                <p>📍 {ride['meeting_point']}</p>
                <p>📅 {date_display} | ⏰ {ride['meeting_time']}</p>
                <p>{ride.get('description', '')}</p>
            </div>
            """, unsafe_allow_html=True)
            
            col1, col2 = st.columns([1, 4])
            with col1:
                if is_registered:
                    if st.button("Leave", key=f"leave_{ride['ride_id']}", type="primary"):
                        if self.ride_manager.remove_participant(ride['ride_id'], str_user_id):
                            st.success("You have left this ride.")
                            st.rerun()
                else:
                    if st.button("Join", key=f"join_{ride['ride_id']}", type="primary"):
                        if self.ride_manager.add_participant(ride['ride_id'], str_user_id):
                            st.success("You have joined this ride!")
                            st.rerun()

    def _show_attendance_marking(self):
        st.markdown('<h1 class="section-header">Mark Attendance</h1>', unsafe_allow_html=True)
        
        # Get rides sorted by date
        rides = self.ride_manager.get_upcoming_rides()
        past_rides = self.ride_manager.get_past_rides()
        all_rides = rides + past_rides
        
        if not all_rides:
            st.info("No rides found.")
            return
            
        for ride in all_rides:
            ride_dates = f"{ride['start_date'].strftime('%Y-%m-%d')} to {ride['end_date'].strftime('%Y-%m-%d')}"
            with st.expander(f"#{ride['ride_id']} - {ride['name']} ({ride_dates})"):
                # Get only the registered users for this ride
                registered_users = self.user_manager.get_registered_users_for_ride(ride['ride_id'])
                
                # If no registered users, show a message and continue to next ride
                if not registered_users:
                    st.warning("No users registered for this ride. Users must join the ride before attendance can be marked.")
                    continue
                
                # Show attendance for each day
                for day in ride['days']:
                    st.subheader(f"Day {day['day']} - {day['date'].strftime('%Y-%m-%d')}")
                    
                    # Current attendance and roles
                    current_attendance = day.get('attendance', [])
                    current_roles = day.get('roles', {})
                    
                    # Convert all IDs to strings for consistent comparison
                    current_attendance = [str(id) for id in current_attendance]
                    
                    # Create list of registered user IDs (as strings)
                    registered_user_ids = [str(user['_id']) for user in registered_users]
                    
                    # Filter default values to ensure they're in the options
                    valid_defaults = [user_id for user_id in current_attendance if user_id in registered_user_ids]
                    
                    selected_users = st.multiselect(
                        f"Select present riders for Day {day['day']}",
                        options=registered_user_ids,
                        default=valid_defaults,
                        format_func=lambda x: next(
                            (user['name'] for user in registered_users if str(user['_id']) == x),
                            str(x)
                        ),
                        key=f"attendance_{ride['ride_id']}_{day['day']}"
                    )
                    
                    # Get eligibility status for registered users only
                    eligibility_map = {}
                    for user in registered_users:
                        # Convert ObjectId to string before passing to _get_eligibility_status
                        user_id_str = str(user['_id'])
                        eligibility_map[user_id_str] = self._get_eligibility_status(user['_id'])
                    
                    # Add eligibility info to the roles section
                    st.markdown("### Role Assignment")
                    
                    # Option for second running pilot
                    has_second_pilot = st.checkbox(
                        "Include a second Running Pilot for this day", 
                        value=day.get('has_second_pilot', False),
                        key=f"second_pilot_option_{ride['ride_id']}_{day['day']}"
                    )
                    
                    # Roles selection
                    col1, col2 = st.columns(2)
                    with col1:
                        # Display eligible status with each role selection
                        st.markdown("#### Lead Rider")
                        for user in registered_users:
                            user_id = str(user['_id'])
                            is_eligible = eligibility_map[user_id]['lead_eligible']
                            badge_class = "eligible" if is_eligible else "not-eligible"
                            st.markdown(f"""
                            <span class="eligibility-badge {badge_class}">
                                {user['name']} - {"✓ Eligible" if is_eligible else "✗ Not Eligible"}
                            </span>
                            """, unsafe_allow_html=True)
                        
                        # Determine default selection for Lead
                        lead_default_index = 0
                        if current_roles.get('lead') in registered_user_ids:
                            lead_default_index = registered_user_ids.index(current_roles.get('lead'))
                            
                        lead = st.selectbox(
                            "Select Lead Rider",
                            options=registered_user_ids,
                            index=lead_default_index if registered_user_ids else None,
                            format_func=lambda x: next(
                                (user['name'] for user in registered_users if str(user['_id']) == x),
                                str(x)
                            ),
                            key=f"lead_{ride['ride_id']}_{day['day']}"
                        )
                        
                        st.markdown("#### Sweep Rider")
                        for user in registered_users:
                            user_id = str(user['_id'])
                            is_eligible = eligibility_map[user_id]['sweep_eligible']
                            badge_class = "eligible" if is_eligible else "not-eligible"
                            st.markdown(f"""
                            <span class="eligibility-badge {badge_class}">
                                {user['name']} - {"✓ Eligible" if is_eligible else "✗ Not Eligible"}
                            </span>
                            """, unsafe_allow_html=True)
                        
                        # Determine default selection for Sweep
                        sweep_default_index = 0
                        if current_roles.get('sweep') in registered_user_ids:
                            sweep_default_index = registered_user_ids.index(current_roles.get('sweep'))
                            
                        sweep = st.selectbox(
                            "Select Sweep Rider",
                            options=registered_user_ids,
                            index=sweep_default_index if registered_user_ids else None,
                            format_func=lambda x: next(
                                (user['name'] for user in registered_users if str(user['_id']) == x),
                                str(x)
                            ),
                            key=f"sweep_{ride['ride_id']}_{day['day']}"
                        )
                    
                    with col2:
                        st.markdown("#### Running Pilot")
                        for user in registered_users:
                            user_id = str(user['_id'])
                            is_eligible = eligibility_map[user_id]['rp_eligible']
                            badge_class = "eligible" if is_eligible else "not-eligible"
                            st.markdown(f"""
                            <span class="eligibility-badge {badge_class}">
                                {user['name']} - {"✓ Eligible" if is_eligible else "✗ Not Eligible"}
                            </span>
                            """, unsafe_allow_html=True)
                        
                        # Determine default selection for Pilot
                        pilot_default_index = 0
                        if current_roles.get('pilot') in registered_user_ids:
                            pilot_default_index = registered_user_ids.index(current_roles.get('pilot'))
                            
                        pilot = st.selectbox(
                            "Select Running Pilot",
                            options=registered_user_ids,
                            index=pilot_default_index if registered_user_ids else None,
                            format_func=lambda x: next(
                                (user['name'] for user in registered_users if str(user['_id']) == x),
                                str(x)
                            ),
                            key=f"pilot_{ride['ride_id']}_{day['day']}"
                        )
                        
                        # Second running pilot if enabled
                        pilot2 = None
                        if has_second_pilot:
                            st.markdown("#### Second Running Pilot")
                            for user in registered_users:
                                user_id = str(user['_id'])
                                is_eligible = eligibility_map[user_id]['rp_eligible']
                                badge_class = "eligible" if is_eligible else "not-eligible"
                                st.markdown(f"""
                                <span class="eligibility-badge {badge_class}">
                                    {user['name']} - {"✓ Eligible" if is_eligible else "✗ Not Eligible"}
                                </span>
                                """, unsafe_allow_html=True)
                            
                            # Determine default selection for Pilot2
                            pilot2_default_index = 0
                            if current_roles.get('pilot2') in registered_user_ids:
                                pilot2_default_index = registered_user_ids.index(current_roles.get('pilot2'))
                                
                            pilot2 = st.selectbox(
                                "Select Second Running Pilot",
                                options=registered_user_ids,
                                index=pilot2_default_index if registered_user_ids else None,
                                format_func=lambda x: next(
                                    (user['name'] for user in registered_users if str(user['_id']) == x),
                                    str(x)
                                ),
                                key=f"pilot2_{ride['ride_id']}_{day['day']}"
                            )
                    
                    if st.button(f"Update Day {day['day']}", key=f"update_day_{ride['ride_id']}_{day['day']}"):
                        roles = {
                            "lead": lead,
                            "sweep": sweep,
                            "pilot": pilot,
                            "pilot2": pilot2 if has_second_pilot else None
                        }
                        if self.ride_manager.update_ride_day(ride['ride_id'], day['day'], selected_users, roles, has_second_pilot):
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

    @st.cache_data(ttl=60)  # Cache for 1 minute
    def _get_eligibility_status(_self, _user_id):
        """Determine rider eligibility based on combined previous and current stats"""
        # Convert ObjectId to string if it's not already a string
        user_id_str = str(_user_id)
        user_id = _user_id  # Keep original for MongoDB query
        
        user = _self.user_manager.db_manager.find_document("users", {"_id": user_id})
        stats = user.get('stats', {})
        
        # Get current participation
        participation = _self.ride_manager.get_user_participation(user_id_str)
        
        # Combine previous and current stats
        total_rides = _self._calculate_total_rides(user_id)
        total_sweeps = stats.get('sweeps', 0) + participation['roles']['sweep']
        total_leads = stats.get('leads', 0) + participation['roles']['lead']
        
        return {
            'sweep_eligible': total_rides >= 10,
            'lead_eligible': total_sweeps >= 3,
            'rp_eligible': total_sweeps >= 3 and total_leads >= 3,
            'stats': {
                'total_rides': total_rides,
                'sweeps': total_sweeps,
                'leads': total_leads,
                'running_pilots': stats.get('running_pilots', 0) + participation['roles']['pilot'] + participation['roles']['pilot2']
            }
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

        # Get all upcoming rides
        upcoming_rides = self.ride_manager.get_upcoming_rides()
        
        if not upcoming_rides:
            st.info("No upcoming rides found.")
            return
            
        # Let user select a specific ride
        ride_options = [(f"#{ride['ride_id']} - {ride['name']} ({ride['start_date'].strftime('%Y-%m-%d')})", ride['ride_id']) 
                    for ride in upcoming_rides]
        
        selected_ride_id = st.selectbox(
            "Select Ride for Pre-ride Report",
            options=[option[1] for option in ride_options],
            format_func=lambda x: next((option[0] for option in ride_options if option[1] == x), "")
        )
        
        selected_ride = next((ride for ride in upcoming_rides if ride['ride_id'] == selected_ride_id), None)
        
        if not selected_ride:
            st.warning("Please select a ride.")
            return
            
        st.markdown(f"## Pre-ride Report for: {selected_ride['name']}")
        st.markdown(f"**Date:** {selected_ride['start_date'].strftime('%Y-%m-%d')} to {selected_ride['end_date'].strftime('%Y-%m-%d')}")
        
        # Get registered users for this ride
        registered_users = self.user_manager.get_registered_users_for_ride(selected_ride_id)
        
        if not registered_users:
            st.warning("No riders have registered for this ride yet.")
            return  # Don't show any data if no riders registered
        
        # Sort users by eligibility (highest to lowest)
        eligibility_data = []
        for user in registered_users:
            eligibility = self._get_eligibility_status(user['_id'])
            eligibility_score = (4 if eligibility['rp_eligible'] else 0) + \
                            (2 if eligibility['lead_eligible'] else 0) + \
                            (1 if eligibility['sweep_eligible'] else 0)
            
            eligibility_data.append({
                'user': user,
                'eligibility': eligibility,
                'score': eligibility_score
            })
        
        # Sort by eligibility score (descending)
        eligibility_data.sort(key=lambda x: x['score'], reverse=True)
        
        # Overview stats
        total_registered = len(registered_users)
        sweep_eligible_count = sum(1 for item in eligibility_data if item['eligibility']['sweep_eligible'])
        lead_eligible_count = sum(1 for item in eligibility_data if item['eligibility']['lead_eligible'])
        rp_eligible_count = sum(1 for item in eligibility_data if item['eligibility']['rp_eligible'])
        
        # Display overview stats
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Registered", total_registered)
        with col2:
            st.metric("Sweep Eligible", sweep_eligible_count)
        with col3:
            st.metric("Lead Eligible", lead_eligible_count)
        with col4:
            st.metric("RP Eligible", rp_eligible_count)
        
        # Create tabs for different roles
        tabs = st.tabs(["All Riders", "Lead Eligible", "Sweep Eligible", "RP Eligible"])
        
        with tabs[0]:
            for item in eligibility_data:
                user = item['user']
                eligibility = item['eligibility']
                stats = eligibility['stats']
                
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
                            <p><strong>Total Rides:</strong> {stats['total_rides']}</p>
                            <p><strong>Total Sweeps:</strong> {stats['sweeps']}</p>
                        </div>
                        <div>
                            <p><strong>Total Leads:</strong> {stats['leads']}</p>
                            <p><strong>Running Pilot Days:</strong> {stats['running_pilots']}</p>
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
        
        # Lead Eligible Tab
        with tabs[1]:
            lead_eligible = [item for item in eligibility_data if item['eligibility']['lead_eligible']]
            if not lead_eligible:
                st.warning("No riders eligible for Lead role.")
            else:
                st.success(f"{len(lead_eligible)} riders are eligible for Lead role.")
                for item in lead_eligible:
                    user = item['user']
                    stats = item['eligibility']['stats']
                    st.markdown(f"""
                    <div style='
                        background-color: rgba(59, 130, 246, 0.2);
                        padding: 20px;
                        border-radius: 10px;
                        margin-bottom: 10px;
                    '>
                        <h3>{user['name']}</h3>
                        <p><strong>Lead Qualification:</strong> {stats['sweeps']} sweeps (minimum 3 required)</p>
                        <p><strong>Phone:</strong> {user['phone']}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
        # Sweep Eligible Tab
        with tabs[2]:
            sweep_eligible = [item for item in eligibility_data if item['eligibility']['sweep_eligible']]
            if not sweep_eligible:
                st.warning("No riders eligible for Sweep role.")
            else:
                st.success(f"{len(sweep_eligible)} riders are eligible for Sweep role.")
                for item in sweep_eligible:
                    user = item['user']
                    stats = item['eligibility']['stats']
                    st.markdown(f"""
                    <div style='
                        background-color: rgba(34, 197, 94, 0.2);
                        padding: 20px;
                        border-radius: 10px;
                        margin-bottom: 10px;
                    '>
                        <h3>{user['name']}</h3>
                        <p><strong>Sweep Qualification:</strong> {stats['total_rides']} rides (minimum 10 required)</p>
                        <p><strong>Phone:</strong> {user['phone']}</p>
                    </div>
                    """, unsafe_allow_html=True)
        
        # Running Pilot Eligible Tab
        with tabs[3]:
            rp_eligible = [item for item in eligibility_data if item['eligibility']['rp_eligible']]
            if not rp_eligible:
                st.warning("No riders eligible for Running Pilot role.")
            else:
                st.success(f"{len(rp_eligible)} riders are eligible for Running Pilot role.")
                for item in rp_eligible:
                    user = item['user']
                    stats = item['eligibility']['stats']
                    st.markdown(f"""
                    <div style='
                        background-color: rgba(234, 179, 8, 0.2);
                        padding: 20px;
                        border-radius: 10px;
                        margin-bottom: 10px;
                    '>
                        <h3>{user['name']}</h3>
                        <p><strong>RP Qualification:</strong> {stats['sweeps']} sweeps and {stats['leads']} leads (minimum 3 each required)</p>
                        <p><strong>Phone:</strong> {user['phone']}</p>
                    </div>
                    """, unsafe_allow_html=True)


def get_mongodb_uri():
    try:
        # Check the environment variable for the current mode
        environment = os.getenv("ENVIRONMENT", "production")
        
        # Development environment
        if environment.lower() == "development":
            dev_uri = os.getenv("MONGODB_URI")
            if dev_uri:
                st.success("Running in development mode")
                return dev_uri

        # Production environment: Check Streamlit secrets
        try:
            uri = st.secrets["MONGODB_URI"]
            if uri:
                # Validate and encode username/password if necessary
                parsed_uri = urllib.parse.urlparse(uri)
                if parsed_uri.username and parsed_uri.password:
                    username = urllib.parse.quote_plus(parsed_uri.username)
                    password = urllib.parse.quote_plus(parsed_uri.password)
                    # Rebuild the URI with encoded credentials
                    encoded_uri = f"{parsed_uri.scheme}://{username}:{password}@{parsed_uri.hostname}"
                    if parsed_uri.port:
                        encoded_uri += f":{parsed_uri.port}"
                    if parsed_uri.path:
                        encoded_uri += f"{parsed_uri.path}"
                    return encoded_uri
                
                if environment.lower() == "production":
                    st.warning(
                        "Connected to production database. Switch to development mode by setting ENVIRONMENT=development"
                    )
                return uri
        except KeyError:
            st.error("No MongoDB URI found in secrets.")
            st.info(
                "To use the development database, set ENVIRONMENT=development and provide MONGODB_URI in your environment variables."
            )
            raise Exception("MongoDB URI not configured")

    except Exception as e:
        st.error(f"Failed to get MongoDB URI: {str(e)}")
        raise e

def reset_password(email_or_phone, new_password, user_manager):
    """Utility function to reset a user's password"""
    try:
        # Find the user
        user = user_manager.db_manager.find_document(
            "users",
            {"$or": [{"phone": email_or_phone}, {"email": email_or_phone}]}
        )
        
        if not user:
            return False, "User not found. Please check your email or phone number."
        
        # Hash the new password
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
        
        # Update the password
        result = user_manager.db_manager.update_document(
            "users",
            {"_id": user["_id"]},
            {"password": hashed_password}
        )
        
        if result.modified_count > 0:
            return True, "Password reset successfully. You can now login with your new password."
        else:
            return False, "Failed to reset password. Please try again."
            
    except Exception as e:
        logging.error(f"Error resetting password: {str(e)}")
        return False, f"An error occurred: {str(e)}"
        
def main():
    st.set_page_config(page_title="Bikers Club", page_icon="🏍️", layout="wide")
    
    try:
        # Initialize managers with the configured URI
        mongodb_uri = get_mongodb_uri()
        db_manager = DatabaseManager(
            uri=mongodb_uri,
            db_name="bikers_club"
        )
        user_manager = UserManager(db_manager)
        ride_manager = RideManager(db_manager)
        dashboard = Dashboard(user_manager, ride_manager)
    except Exception as e:
        st.error("Failed to initialize application. Please check your configuration.")
        st.exception(e)
        return
        
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
        st.markdown("""
        <div style="text-align: center; margin-bottom: 30px;">
            <h1 style="color: #7c3aed; font-size: 3rem; margin-bottom: 0;">🏍️ Bikers Club</h1>
            <p style="font-size: 1.2rem; color: #a0a0a0;">Connect, Ride, Share Adventures</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Create column layout for a better visual design
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.markdown("""
            <div style="background: linear-gradient(135deg, rgba(124, 58, 237, 0.1), rgba(124, 58, 237, 0.3)); 
                        padding: 30px; border-radius: 15px; margin-bottom: 20px; height: 100%;">
                <h2 style="color: #7c3aed; margin-bottom: 20px;">Why Join Bikers Club?</h2>
                <ul style="list-style-type: none; padding-left: 0;">
                    <li style="margin-bottom: 15px; display: flex; align-items: center;">
                        <span style="color: #7c3aed; font-size: 1.5rem; margin-right: 10px;">🛣️</span> 
                        <span>Organized group rides with experienced leaders</span>
                    </li>
                    <li style="margin-bottom: 15px; display: flex; align-items: center;">
                        <span style="color: #7c3aed; font-size: 1.5rem; margin-right: 10px;">👥</span> 
                        <span>Connect with fellow motorcycle enthusiasts</span>
                    </li>
                    <li style="margin-bottom: 15px; display: flex; align-items: center;">
                        <span style="color: #7c3aed; font-size: 1.5rem; margin-right: 10px;">🏆</span> 
                        <span>Track your riding progress and achievements</span>
                    </li>
                    <li style="margin-bottom: 15px; display: flex; align-items: center;">
                        <span style="color: #7c3aed; font-size: 1.5rem; margin-right: 10px;">🔒</span> 
                        <span>Safety-focused rides with experienced sweeps and pilots</span>
                    </li>
                </ul>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            tab1, tab2, tab3 = st.tabs(["Login", "Register", "Forgot Password"])
            
            with tab1:
                with st.form("login", border=False):
                    st.markdown('<h3 style="margin-bottom: 20px;">Welcome Back!</h3>', unsafe_allow_html=True)
                    phone_or_email = st.text_input("Phone or Email", placeholder="Enter your phone or email")
                    password = st.text_input("Password", type="password", placeholder="Enter your password")
                    
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        submit = st.form_submit_button("Login", use_container_width=True)
                    
                    if submit:
                        if not phone_or_email or not password:
                            st.error("Please enter both phone/email and password.")
                        else:
                            user = user_manager.authenticate_user(phone_or_email, password)
                            if user:
                                st.session_state.user = user
                                st.success("Login successful!")
                                st.rerun()
                            else:
                                st.error("Invalid credentials. Please try again.")
            
            with tab2:
                with st.form("register", border=False):
                    st.markdown('<h3 style="margin-bottom: 20px;">Create New Account</h3>', unsafe_allow_html=True)
                    
                    # Basic Information
                    name = st.text_input("Name", placeholder="Enter your full name")
                    phone = st.text_input("Phone", placeholder="Enter your phone number")
                    email = st.text_input("Email", placeholder="Enter your email address")
                    
                    # Emergency Contact - Now with name and phone
                    st.markdown("### Emergency Contact Information")
                    emergency_name = st.text_input("Emergency Contact Name", placeholder="Name of emergency contact")
                    emergency_contact = st.text_input("Emergency Contact Phone", placeholder="Phone number for emergencies")
                    
                    password = st.text_input("Password", type="password", placeholder="Create a strong password")
                    confirm_password = st.text_input("Confirm Password", type="password", placeholder="Confirm your password")
                    
                    # Previous Creedbot User - Using expandable section instead of checkbox
                    with st.expander("📊 Are you a user of the previous Creedbot? Click here to enter your riding history"):
                        st.info("If you were using the previous Creedbot system, enter your riding history below to migrate your stats.")
                        
                        is_existing = True  # Set to true when this section is used
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
                    
                    # Registration button
                    if st.form_submit_button("Register", use_container_width=True):
                        try:
                            # Combine emergency contact info
                            full_emergency_contact = f"{emergency_name}: {emergency_contact}"
                            
                            # Validation
                            if not all([name, phone, emergency_name, emergency_contact, email, password]):
                                st.error("All fields are required")
                            elif password != confirm_password:
                                st.error("Passwords do not match")
                            else:
                                # Determine if user is from previous system
                                is_from_previous = st.session_state.get('_is_expander_open', False) and (
                                    previous_rides > 0 or sweeps > 0 or leads > 0 or 
                                    running_pilots > 0 or ride_marshals > 0
                                )
                                
                                # Create user
                                user_id = user_manager.create_user(
                                    name=name,
                                    phone=phone,
                                    emergency_contact=full_emergency_contact,
                                    email=email,
                                    password=password,
                                    is_existing_user=is_from_previous,
                                    previous_stats=previous_stats if is_from_previous else None,
                                    previous_rides=previous_rides if is_from_previous else None
                                )
                                
                                success_message = "Registration successful! Please login."
                                if user_manager._is_first_user():
                                    success_message += " You have been granted admin privileges as the first user."
                                
                                st.success(success_message)
                                
                                st.rerun()
                        except ValueError as e:
                            st.error(str(e))
            
            with tab3:
                with st.form("forgot_password", border=False):
                    st.markdown('<h3 style="margin-bottom: 20px;">Reset Password</h3>', unsafe_allow_html=True)
                    st.info("Enter your email or phone number to reset your password.")
                    
                    email_or_phone = st.text_input("Email or Phone", placeholder="Enter your registered email or phone")
                    new_password = st.text_input("New Password", type="password", placeholder="Enter your new password")
                    confirm_new_password = st.text_input("Confirm New Password", type="password", placeholder="Confirm your new password")
                    
                    if st.form_submit_button("Reset Password", use_container_width=True):
                        if not email_or_phone or not new_password or not confirm_new_password:
                            st.error("Please fill in all fields.")
                        elif new_password != confirm_new_password:
                            st.error("Passwords do not match.")
                        else:
                            success, message = reset_password(email_or_phone, new_password, user_manager)
                            if success:
                                st.success(message)
                            else:
                                st.error(message)

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
