import pymongo
from pymongo import MongoClient
from bson import ObjectId
from typing import Dict, Any
from datetime import datetime

class CreedDatabaseSetup:
    def __init__(self, connection_string: str = "mongodb://localhost:27017/"):
        """
        Initialize database connection and create collections
        """
        self.client = MongoClient(connection_string)
        self.db = self.client["creed_management"]
        
        # Collections
        self.users = self.db["users"]
        self.rides = self.db["rides"]
        self.master = self.db["master"]  # Replace events with master
        
        # Create indexes and enforce schemas
        self._create_user_schema()
        self._create_ride_schema()
        self._create_master_schema()

    def _create_user_schema(self):
        """
        Create user schema with comprehensive validation
        """
        user_validator = {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["email", "firstName", "lastName", "password"],
                "properties": {
                    "email": {
                        "bsonType": "string",
                        "pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
                        "description": "Must be a valid email address"
                    },
                    "firstName": {
                        "bsonType": "string",
                        "minLength": 2,
                        "maxLength": 50
                    },
                    "lastName": {
                        "bsonType": "string",
                        "minLength": 2,
                        "maxLength": 50
                    },
                    "password": {
                        "bsonType": "string",
                        "description": "Must be a hashed password"
                    },
                    "phoneNumber": {
                        "bsonType": "string",
                        "pattern": r"^\+?[1-9]\d{1,14}$"
                    },
                    "roles": {
                        "bsonType": "object",
                        "properties": {
                            "isRider": {"bsonType": "bool"},
                            "isFlagHolder": {"bsonType": "bool"},
                            "isManagingCommittee": {"bsonType": "bool"}
                        }
                    },
                    "performanceMetrics": {
                        "bsonType": "object",
                        "properties": {
                            "totalRides": {"bsonType": "int"},
                            "sweepsCount": {"bsonType": "int"},
                            "leadsCount": {"bsonType": "int"},
                            "flagHolderCount": {"bsonType": "int"}
                        }
                    }
                }
            }
        }
        
        # Create collection with validator
        self.db.create_collection("users", validator=user_validator)
        
        # Create unique index for email
        self.users.create_index("email", unique=True)

    def _create_ride_schema(self):
        """
        Create ride schema with comprehensive validation
        """
        ride_validator = {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["rideName", "startDate", "creator", "status"],
                "properties": {
                    "rideName": {
                        "bsonType": "string",
                        "minLength": 3,
                        "maxLength": 100
                    },
                    "startDate": {
                        "bsonType": "date"
                    },
                    "endDate": {
                        "bsonType": "date"
                    },
                    "creator": {
                        "bsonType": "string"
                    },
                    "status": {
                        "enum": ["Planned", "Confirmed", "Ongoing", "Completed", "Cancelled"]
                    },
                    "totalKilometers": {
                        "bsonType": "double",
                        "minimum": 0
                    },
                    "registeredRiders": {
                        "bsonType": "array",
                        "items": {
                            "bsonType": "object",
                            "properties": {
                                "userId": {"bsonType": "string"},
                                "role": {"enum": ["rider", "sweep", "lead", "rp"]}
                            }
                        }
                    }
                }
            }
        }
        
        self.db.create_collection("rides", validator=ride_validator)
        
        # Indexes for performance
        self.rides.create_index("creator")
        self.rides.create_index("startDate")
        self.rides.create_index("status")

    def _create_master_schema(self):
        """
        Create master schema for tracking pre-ride information and ride counts
        """
        master_validator = {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["type", "data"],
                "properties": {
                    "type": {
                        "enum": [
                            "ride_setup_checklist", 
                            "pre_ride_documentation", 
                            "ride_statistics",
                            "role_eligibility_criteria"
                        ]
                    },
                    "data": {
                        "bsonType": "object"
                    },
                    "createdAt": {
                        "bsonType": "date"
                    },
                    "updatedAt": {
                        "bsonType": "date"
                    }
                }
            }
        }
        
        self.db.create_collection("master", validator=master_validator)

    def get_ride_counts(self, ride_id: str) -> Dict[str, int]:
        """
        Retrieve counts for different rider roles in a specific ride
        """
        ride = self.rides.find_one({"_id": ObjectId(ride_id)})
        if not ride:
            return {
                "total_riders": 0,
                "sweep_count": 0,
                "lead_count": 0,
                "running_pilot_count": 0
            }
        
        registered_riders = ride.get("registeredRiders", [])
        
        return {
            "total_riders": len(registered_riders),
            "sweep_count": sum(1 for rider in registered_riders if rider.get("role") == "sweep"),
            "lead_count": sum(1 for rider in registered_riders if rider.get("role") == "lead"),
            "running_pilot_count": sum(1 for rider in registered_riders if rider.get("role") == "rp")
        }

    def close_connection(self):
        """
        Close the MongoDB connection
        """
        self.client.close()

def initialize_database():
    """
    Initialize the database with predefined schemas
    """
    try:
        db_setup = CreedDatabaseSetup()
        print("Database and collections created successfully!")
        db_setup.close_connection()
    except Exception as e:
        print(f"Error initializing database: {e}")

if __name__ == "__main__":
    initialize_database()