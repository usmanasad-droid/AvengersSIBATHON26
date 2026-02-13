import sys
sys.path.append(r"E:\Study Planner\libs")
print("MySQL connector loaded successfully")

import mysql.connector

def get_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="YOUR_MYSQL_PASSWORD",
        database="study_planner"
    )
