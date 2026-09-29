import pymysql

# Django refuses MySQLdb versions below 2.2.1; PyMySQL reports 1.4.6 by default.
pymysql.version_info = (2, 2, 1, 'final', 0)
pymysql.install_as_MySQLdb()
