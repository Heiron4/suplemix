-- SupleMix database (PostgreSQL). Tables are also created automatically when the app starts.
-- Tables: tbl_user, nutrition_target_tbl, nutrition_log_tbl, mix_tbl

CREATE TABLE tbl_user (
	user_id SERIAL NOT NULL, 
	username VARCHAR(500) NOT NULL, 
	password VARCHAR(100) NOT NULL, 
	email VARCHAR(500) NOT NULL, 
	role VARCHAR(10) NOT NULL, 
	gender VARCHAR(6), 
	weight FLOAT NOT NULL, 
	height FLOAT NOT NULL, 
	birth_date DATE, 
	workout_routine VARCHAR(500), 
	goal VARCHAR(100), 
	PRIMARY KEY (user_id), 
	UNIQUE (username), 
	UNIQUE (email)
);

CREATE TABLE mix_tbl (
	mix_id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	mix_name VARCHAR(200) NOT NULL, 
	mix_date DATE NOT NULL, 
	mix_time VARCHAR(5) NOT NULL, 
	whey_protein FLOAT NOT NULL, 
	creatine FLOAT NOT NULL, 
	preworkout FLOAT NOT NULL, 
	auto_dispense BOOLEAN NOT NULL, 
	PRIMARY KEY (mix_id), 
	FOREIGN KEY(user_id) REFERENCES tbl_user (user_id)
);

CREATE INDEX ix_mix_tbl_user_id ON mix_tbl (user_id);

CREATE TABLE nutrition_log_tbl (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	meal_name VARCHAR(200) NOT NULL, 
	log_date DATE NOT NULL, 
	log_time VARCHAR(5) NOT NULL, 
	calories FLOAT NOT NULL, 
	protein FLOAT NOT NULL, 
	carbs FLOAT NOT NULL, 
	fat FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES tbl_user (user_id)
);

CREATE INDEX ix_nutrition_log_tbl_user_id ON nutrition_log_tbl (user_id);

CREATE TABLE nutrition_target_tbl (
	nutrition_target_id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	calories FLOAT NOT NULL, 
	protein FLOAT NOT NULL, 
	carbs FLOAT NOT NULL, 
	fat FLOAT NOT NULL, 
	PRIMARY KEY (nutrition_target_id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES tbl_user (user_id)
);
