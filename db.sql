CREATE DATABASE IF NOT EXISTS study_planner;
USE study_planner;

CREATE TABLE IF NOT EXISTS users (
  user_id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  email VARCHAR(255) NOT NULL UNIQUE,
  password VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
select * from users;
USE study_planner;

-- ---------------- Subjects ----------------
CREATE TABLE IF NOT EXISTS subjects (
    subject_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    subject_name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, subject_name)
);

-- ---------------- Topics ----------------
CREATE TABLE IF NOT EXISTS topics (
    topic_id INT AUTO_INCREMENT PRIMARY KEY,
    subject_id INT NOT NULL,
    topic_name VARCHAR(255) NOT NULL,
    difficulty_level TINYINT NOT NULL CHECK (difficulty_level BETWEEN 1 AND 5),
    importance TINYINT NOT NULL CHECK (importance BETWEEN 1 AND 5),
    confidence_level TINYINT NOT NULL CHECK (confidence_level BETWEEN 1 AND 5),
    hours_required DECIMAL(4,1) DEFAULT 1.0,
    last_studied DATE DEFAULT NULL,
    times_studied INT DEFAULT 0,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    UNIQUE(subject_id, topic_name)
);

-- ---------------- Exams ----------------
CREATE TABLE IF NOT EXISTS exams (
    exam_id INT AUTO_INCREMENT PRIMARY KEY,
    subject_id INT NOT NULL,
    exam_name VARCHAR(255) NOT NULL,
    exam_date DATE NOT NULL,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE
);

-- ---------------- Study Sessions ----------------
CREATE TABLE IF NOT EXISTS study_sessions (
    session_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    topic_id INT NOT NULL,
    scheduled_date DATE NOT NULL,
    scheduled_time TIME NOT NULL,
    duration_minutes INT NOT NULL,
    status ENUM('pending','completed','skipped') DEFAULT 'pending',
    completion_date DATETIME DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE,
    INDEX(user_id, scheduled_date)
);

-- ---------------- User Preferences ----------------
CREATE TABLE IF NOT EXISTS user_preferences (
    preference_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    daily_study_hours FLOAT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);


select * from subjects;
select * from topics;
select * from exams;
select * from user_preferences;