import sys
import os
import ctypes
import configparser

from time import time, sleep
from datetime import datetime
from lib.inverter import InverterNotFoundError, InverterFinder, KeepAliveInverter
import mysql.connector as mysql
from reprint import output

"""Отключение рамки консоли"""

"""Отключение взаимодействия с консолью(предотвращение замерзания выводимых данных)"""
kernel32 = ctypes.windll.kernel32
kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 128)
"""Отключение мигающего курсора"""
class _CursorInfo(ctypes.Structure):_fields_ = [("size", ctypes.c_int), ("visible", ctypes.c_byte)]
ci = _CursorInfo()
handle = ctypes.windll.kernel32.GetStdHandle(-11)
ctypes.windll.kernel32.GetConsoleCursorInfo(handle, ctypes.byref(ci))
ci.visible = False
ctypes.windll.kernel32.SetConsoleCursorInfo(handle, ctypes.byref(ci))

"""Мониторинг"""
def monitor(interval: float):
    config = configparser.ConfigParser()
    config.read("settings.ini")

    def _format_two_tuple(t):
        width = max([len(k) for k, v in t])
        rows = ['{:.<{width}}...{}'.format(k, v, width=width) for k, v in t]
        return '\n'.join(rows)

    def Start():
        os.system("CLS")
        with output(output_type='list', initial_len=26, interval=0) as output_list:

            def DB_Wrire():
                query = "INSERT INTO invertor_dump (`pv1_input_power(Вт)`, `pv1_voltage(В)`, `pv1_current(А)`, `pv2_input_power(Вт)`, `pv2_voltage(В)`, `pv2_current(А)`, `output_power(Вт)`, `grid_voltage(В)`, `grid_current(А)`, `grid_frequency(Гц)`, `energy_today(кВт*ч)`, `energy_total(кВт*ч)`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);"
                values = [status_dict['pv1_input_power'], \
                          status_dict['pv1_voltage'], \
                          status_dict['pv1_current'], \
                          status_dict['pv2_input_power'], \
                          status_dict['pv2_voltage'], \
                          status_dict['pv2_current'], \
                          status_dict['output_power'], \
                          status_dict['grid_voltage'], \
                          status_dict['grid_current'], \
                          status_dict['grid_frequency'], \
                          status_dict['energy_today'], \
                          status_dict['energy_total']]
                cursor.execute(query, values)
                db.commit()
                cursor.close()

            """Вывод статичной информации в консоль и подготовка динамики для дальнейшего обновления"""
            output_list[0] = "*" * 89
            output_list[1] = "*" + " " * 87 + "*"
            output_list[2] = "*    Статус системы: " + " " * 42 + "Время/дата статуса:      *"
            output_list[3] = "*" + " " * 62 + "|| " + datetime.now().strftime("%Y.%m.%d %H:%M.%S") + "   *"
            output_list[4] = "*    Сетевой адрес инвертора:" + " " * 59 + "*"
            output_list[5] = "*" + " " * 62 + "Текущее время/дата:      *"
            output_list[6] = "*" + " " * 62 + "|| Время: " + datetime.now().strftime("%H:%M.%S") + "       *"
            output_list[7] = "*" + " " * 62 + "|| Дата: " + datetime.now().strftime("%Y.%m.%d") + "      *"
            output_list[8] = "*" + " " * 87 + "*"
            output_list[9] = "*" * 89
            output_list[10] = " " * 35 + "\x1B[" + "33;1m" + "Солнечный каскад №1 (8x200Вт)" + "\x1B[" + "0m"
            output_list[11] = " " + " " * 13 + "Мощность" + " " * 17 + "Напряжение" + " " * 17 + "Ток"
            output_list[12] = " "
            output_list[13] = "*" * 89
            output_list[14] = " " * 35 + "\x1B[" + "33;1m" + "Солнечный каскад №2 (8x200Вт)" + "\x1B[" + "0m"
            output_list[15] = " " + " " * 13 + "Мощность" + " " * 17 + "Напряжение" + " " * 17 + "Ток"
            output_list[16] = " "
            output_list[17] = "*" * 89
            output_list[18] = " " * 42 + "\x1B[" + "33;1m" + "Сеть" + "\x1B[" + "0m"
            output_list[19] = " " + " " * 13 + "Частота" + " " * 18 + "Напряжение" + " " * 17 + "Ток"
            output_list[20] = " "
            output_list[21] = "*" * 89
            output_list[22] = " " * 40 + "\x1B[" + "33;1m" + "Выработка" + "\x1B[" + "0m"
            output_list[23] = " " + " " * 7 + "Выдаваемая мощность" + " " * 12 + "Эн.Сегодня" + " " * 15 + "Эн.Общее"
            output_list[24] = " "
            output_list[25] = "*" * 89

            """Поиск инвертора"""
            with InverterFinder(interface_ip='') as finder:
                output_list[2] = "*    Статус системы: " + "\x1B[" + "33;1m" + "Поиск инвертора..." + "\x1B[" + "0m" + "                        Время/дата статуса:      *"
                try:
                    inverter = KeepAliveInverter(*finder.find_inverter())
                except InverterNotFoundError:
                    output_list[2] = "*    Статус системы: " + "\x1b[" + "31;1m" + "Инверторное оборудование не найдено" + "\033[" + "0m" + "       Время/дата статуса:      *"
                    return

            """Обработка данных после подключения к инвертору"""
            with inverter:
                #output[1] = "1"
                output_list[4] = "*    Сетевой адрес инвертора: {}".format(inverter.addr) + "                                    *"
                t = time()
                anim = ["▀▄","▄▀"]
                pos = False
                while True:
                    """Вывод времени/даты и анимация работы"""
                    pos= not pos
                    output_list[2] = "* " + "\x1B[" + "33;1m" + anim[int(pos)] + "\x1B[" + "0m" + " Статус системы: " + "\x1b[" + "32;1m" + "Мониторинг" + "\033[" + "0m" + "                                Время/дата статуса:      *"
                    output_list[6] = "*" + " "*62 + "|| Время: " + datetime.now().strftime("%H:%M.%S") + "       *"
                    output_list[7] = "*" + " "*62 + "|| Дата: " + datetime.now().strftime("%Y.%m.%d") + "      *"

                    """Получение данных"""
                    status_dict = inverter.status()

                    """Вывод динамических данных в консоль"""
                    pv1_base = 17-(len(str(status_dict['pv1_input_power'])+"(Вт)")//2)
                    pv1_p = 26 - (len(str(status_dict['pv1_voltage'])+"(В)")//2) - (len(str(status_dict['pv1_input_power'])+"(Вт)")//2)
                    pv1_v = 24 - (len(str(status_dict['pv1_current'])+"(А)")//2) - (len(str(status_dict['pv1_voltage'])+"(В)")//2)
                    output_list[12] = " " + " " * pv1_base + str(status_dict['pv1_input_power']) + "(Вт)" + " " *pv1_p + str(status_dict['pv1_voltage']) + "(В)" + " " * pv1_v + str(status_dict['pv1_current']) +"(А)"

                    pv2_base = 17 - (len(str(status_dict['pv2_input_power']) + "(Вт)") // 2)
                    pv2_p = 25 - (len(str(status_dict['pv2_voltage']) + "(В)") // 2) - (len(str(status_dict['pv2_input_power']) + "(Вт)") // 2)
                    pv2_v = 24 - (len(str(status_dict['pv2_current']) + "(А)") // 2) - (len(str(status_dict['pv2_voltage']) + "(В)") // 2)
                    output_list[16] = " " + " " * pv2_base + str(status_dict['pv2_input_power']) + "(Вт)" + " " * pv2_p + str(status_dict['pv2_voltage']) + "(В)" + " " * pv2_v + str(status_dict['pv2_current']) + "(А)"

                    grid_base = 17 - (len(str(status_dict['grid_frequency']) + "(Гц)") // 2)
                    grid_f = 25 - (len(str(status_dict['grid_voltage']) + "(В)") // 2) - (len(str(status_dict['grid_frequency']) + "(Гц)") // 2)
                    grid_v = 24 - (len(str(status_dict['grid_current']) + "(А)") // 2) - (len(str(status_dict['grid_voltage']) + "(В)") // 2)
                    output_list[20] = " " + " " * grid_base + str(status_dict['grid_frequency']) + "(Гц)" + " " * grid_f + str(status_dict['grid_voltage']) + "(В)" + " " * grid_v + str(status_dict['grid_current']) + "(А)"

                    inverter_base = 17 - (len(str(status_dict['output_power']) + "(Вт)") // 2)
                    inverter_d = 25 - (len(str(status_dict['energy_today']) + "(кВт*ч)") // 2) - (len(str(status_dict['output_power']) + "(Вт)") // 2)
                    inverter_t = 24 - (len(str(status_dict['energy_total']) + "(кВт*ч)") // 2) - (len(str(status_dict['energy_today']) + "(кВт*ч)") // 2)
                    output_list[24] = " " + " " * inverter_base + str(status_dict['output_power']) + "(Вт)" + " " * inverter_d + str(status_dict['energy_today']) + "(кВт*ч)" + " " * inverter_t + str(status_dict['energy_total']) + "(кВт*ч)"

                    """Загрузка конфига с параметрами для подключения к базе данных"""
                    config.read("settings.ini")

                    """
                    Отправка данных мониторинга в базу данных при условии активности рабочего режима инвертора
                    Инвертор может выйти из данного режима в отсутствии выдаваемой солнечными панелями мощности.    
                    """
                    if status_dict['operation_mode'] == "Normal":
                        try:
                            cursor = db.cursor()
                            DB_Wrire()
                        except mysql.connection.errors.IntegrityError:
                            pass
                        except:
                            try:
                                config.read("settings.ini")
                                db = mysql.connect(
                                    host=config["SolarRiver"]["host"],
                                    port=config["SolarRiver"]["port"],
                                    user=config["SolarRiver"]["user"],
                                    passwd=config["SolarRiver"]["passwd"],
                                    database=config["SolarRiver"]["database"]
                                )
                                cursor = db.cursor()
                            except:
                                pass
                    t += interval
                    sleep(max(t - time(), 0))
    while True:
        try:
            Start()
        except:
            pass

monitor(1)
