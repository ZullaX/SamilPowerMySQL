import logging
import socket
import sys
from collections import OrderedDict
from threading import Event, Thread
from time import sleep
from typing import Tuple, Dict, BinaryIO, Any, Optional

from lib.statustypes import status_types


class Inverter:
    """Предоставляет методы связи с подключенным инвертором.
    
    Чтобы открыть соединение с инвертором, см. Класс InverterListener.
    Методы запроса синхронны и возвращают ответ. Когда
    соединение потеряно, исключение возникает в следующий раз, когда запрос
    сделан.
    
    Методы не являются потокобезопасными.
    """
    
    # Кэширует формат сообщений о состоянии инвертора
    _status_format = None

    def __init__(self, sock: socket, addr):
        """Конструктор.

        Args:
            sock: Сокет инвертора, которое предполагается подключить
            addr: сетевой адрес инвертора (в настоящее время не используется)
        """
        self.sock = sock
        self.sock_file = sock.makefile('rwb')
        self.addr = addr
        # Инверторы должны среагировать примерно через 1,5 секунды, установив тайм-аут
        # выше этого значения гарантирует, что приложение тоже не зависнет
        # долго, когда инвертор ничего не отправляет.
        self.sock.settimeout(30.0)

    def __enter__(self):
        """Returns self."""
        return self

    def __exit__(self, *args):
        """See self.disconnect."""
        self.disconnect()

    def disconnect(self) -> None:
        """Отправляет пакет выключения и закрывает соединение.

        socket.shutdown отправляет пакет отключения инвертору, который аккуратно
        закрывает соединение и позволяет инвертору напрямую принимать новые
        соединения.
        """
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError as e:
            # Возможно, сокет уже был закрыт по какой-то причине
            # В этом случае будет сгенерирована ошибка OSError:
            #
            # * [Errno 9] Плохой дескриптор файла
            # * [WinError 10038] Была предпринята попытка операции с чем-то, что не является сокетом
            if e.errno != 107 and e.errno != 9 and e.errno != 10038:
                raise e
        self.sock_file.close()
        self.sock.close()

    def status(self) -> Dict:
        """Получает данные о текущем состоянии от инвертора.
        
        Примеры ключей словаря: pv1_input_power, output_power,
        energy_today. Значения обычно имеют тип int или decimal.Decimal.
        
        Все возможные значения см. в statustypes.py.
        """
        if not self._status_format:
            # Формат статуса извлечения и кэширования
            self._status_format = self.status_format()

        ident, payload = self.request(b'\x01\x02\x02', b'', b'\x01\x82')

        # Полезная нагрузка должна быть в два раза больше формата статуса
        if 2 * len(self._status_format) != len(payload):
            logging.warning("Size of status payload and format differs, format %s, payload %s",
                            self._status_format.hex(), payload.hex())

        # Получить все значения типа данных статуса
        status_values = OrderedDict()
        for name, type_def in status_types.items():
            val = type_def.get_value(self._status_format, payload)
            if val is not None:
                status_values[name] = val
        return status_values

    def status_format(self):
        """Получает формат, используемый для сообщений данных о состоянии от инвертора.
        
        Подробнее см. Информацию о протоколе.
        """
        ident, payload = self.request(b'\x01\x00\x02', b'', b'\x01\x80')
        return payload

    def request(self, identifier: bytes, payload: bytes, expected_response_id=b"") -> Tuple[bytes, bytes]:
        """Отправляет сообщение и возвращает полученный ответ.

        Args:
            identifier: идентификатор сообщения (заголовок).
            payload: полезная нагрузка сообщения.
            expected_response_id: Идентификатор ответа проверяется, чтобы увидеть
                начинается ли оно с указанного здесь значения. Если это не так,
                возникает исключение.

        Returns:
            Кортеж с идентификатором и полезной нагрузкой.
        """
        self.send(identifier, payload)
        response_id, response_payload = self.receive()
        while not response_id.startswith(expected_response_id):
            logging.warning("Получен неожиданный ответ инвертора {} на запрос {}".format(
                response_id.hex(), identifier.hex()))
            response_id, response_payload = self.receive()
        return response_id, response_payload

    def send(self, identifier: bytes, payload: bytes):
        """Создает и отправляет сообщение инвертору.

        Raises:
            BrokenPipeError: При закрытии соединения.
            ValueError: Когда соединение уже было закрыто, с сообщением
                'запись в закрытый файл'.
        """
        message = construct_message(identifier, payload)
        logging.debug('Отправка %s', message.hex())
        self.sock_file.write(message)
        self.sock_file.flush()

    def receive(self) -> Tuple[bytes, bytes]:
        """Читает и возвращает следующее сообщение от инвертора.

        см. read_message.
        """
        return read_message(self.sock_file)


class InverterFinder:
    """Класс для установления новых подключений инвертора.
    
    Вам нужно вызвать open() и close() или использовать класс в операторе with
    """

    listen_sock = None  # type: Optional[socket.socket]

    def __init__(self, interface_ip=''):
        """Создать экземпляр.

        Args:
            interface_ip: привязать IP-адрес интерфейса для подслушивающих и широковещания сокетов.
        """
        self.interface_ip = interface_ip

    def __enter__(self):
        """См. open метод."""
        self.open_with_retries()
        return self

    def __exit__(self, *args):
        """См. close метод."""
        self.close()

    def open(self):
        """Создает и связывает сокет слушателя, далее начинает прослушивание.

        Необходимо вызвать перед поиском инверторов, если они не используются в качестве
        менеджер контекста.
        """
        if self.listen_sock:
            raise RuntimeError("Сокет уже создан")

        try:
            # Создание сокета
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Разрешить конфликты привязки сокетов, это дает возможность напрямую перепривязаться к тому же порту.
            if sys.platform == 'win32':
                # Windows ведет себя по-другому, нужен этот вместо SO_REUSEADDR
                self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            self.listen_sock = None
            raise

        try:
            # Привязка и прослушивание, привязка может вызвать ошибку OSError, если порт уже привязан
            self.listen_sock.bind((self.interface_ip, 1200))
            self.listen_sock.listen()
        except OSError:
            self.listen_sock.close()
            self.listen_sock = None
            raise

    def open_with_retries(self, retries=10, period=1.0):
        """Открывает поисковик с повторной попыткой, если порт уже привязан.

        Args:
            retries: Максимальное количество повторных попыток, когда порт прослушивателя привязан.
            period: Период между попытками.

        Raises:
            OSError: Когда все попытки завершились неудачно.
        """
        tries = 0
        while True:
            try:
                self.open()
                return
            except OSError as e:
                # Повторит попытку, если данная ошибка не равна «порт уже привязан» (98) или его вариант для Windows (10048).
                if e.errno != 98 and e.errno != 10048:
                    raise
                logging.info("Порт прослушивания (1200) уже используется, повторная попытка")
                # Проверьте максимальное количество повторных попыток
                tries += 1
                if tries >= retries:
                    raise
                sleep(period)
        # (Это недостижимо)

    def close(self):
        """Закрывает сокет слушателя."""
        self.listen_sock.close()
        self.listen_sock = None

    def find_inverter(self, advertisements=10, interval=5.0) -> Tuple[socket.socket, Any]:
        """Поиск инвертора в сети.

        Args:
            interval: Время между каждым поисковым сообщением/объявлением.
            advertisements: Количество отправляемых сообщений.

        Returns:
            Кортеж с сокетом и адресом инвертора, такой же, как и тот, который
            socket.accept(). Может быть использован для создания инвертора
            экземпляра.

        Raises:
            InverterNotFoundError: Если инвертор не был найден после того, как все поисковые
                сообщения были отправлены.
        """
        message = construct_message(b'\x00\x40\x02', b'I AM SERVER')
        self.listen_sock.settimeout(interval)
        # Сокет вещания
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as bc:
            bc.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            bc.bind((self.interface_ip, 0))

            for i in range(advertisements):
                logging.debug('Отправка широковещательного сообщения сервера')
                bc.sendto(message, ('<broadcast>', 1300))
                try:
                    sock, addr = self.listen_sock.accept()
                    logging.info('Подключен с инвертором по адресу %s', addr)
                    return sock, addr
                except socket.timeout:
                    pass
        raise InverterNotFoundError


def decode_string(val: bytes) -> str:
    """Декодирует последовательность байтов с завершающим нулем в строку с использованием ASCII и удаляет пробелы."""
    return val.partition(b'\x00')[0].decode('ascii').strip()


def calculate_checksum(message: bytes) -> bytes:
    """Вычисляет контрольную сумму для сообщения.
    
    Сообщение не должно иметь контрольной суммы, добавленной к нему.

    Returns:
        Контрольную сумму в виде последовательности байтов длиной 2.
    """
    return sum(message).to_bytes(2, byteorder='big')


def construct_message(identifier: bytes, payload: bytes) -> bytes:
    """Конструирует сообщение для инвертора из идентификатора и полезной нагрузки."""
    start = b'\x55\xaa'
    payload_size = len(payload).to_bytes(2, byteorder='big')
    message = start + identifier + payload_size + payload
    checksum = calculate_checksum(message)
    return message + checksum


def read_message(stream: BinaryIO) -> Tuple[bytes, bytes]:
    """Считывает следующее сообщение инвертора из файлоподобного объекта/потока.

    Returns:
        Кортеж с идентификатором и полезной нагрузкой сообщения.

    Raises:
        InverterEOFError: При потере соединения (встречается EOF).
        ValueError: Когда сообщение имеет неправильный формат, например, контрольная сумма является
            недействительна или первые два байта не являются '55 aa'.
    """
    # Начало сообщения + проверка EOF
    start = stream.read(2)
    # if start == b"":
    #     raise InverterEOFError
    # if start != b"\x55\xaa":
    #     raise ValueError("Invalid start of message")

    # Идентификатор
    identifier = stream.read(3)

    # Полезная нагрузка
    payload_size_bytes = stream.read(2)
    payload_size = int.from_bytes(payload_size_bytes, byteorder='big')
    if payload_size < 0 or payload_size > 4096:  # Проверка правильности для странных значений размера полезной нагрузки
        raise ValueError("Неожиданное значение размера полезной нагрузки")
    payload = stream.read(payload_size)

    # Контрольная сумма
    checksum = stream.read(2)
    message = start + identifier + payload_size_bytes + payload
    # if checksum != calculate_checksum(message):
    #     raise ValueError('Контрольная сумма сообщения недействительна %s', message.hex())

    return identifier, payload


class InverterNotFoundError(Exception):
    """В сети не обнаружен инвертор."""
    pass


class InverterEOFError(Exception):
    """Связь с инвертором была потеряна.
    
    Возникает, когда встречается EOF.
    """
    pass


class KeepAliveInverter(Inverter):
    """Инвертор, который поддерживается в живом состоянии путем отправки запроса каждые несколько секунд.
    
    Сообщения keep-alive отправляются только тогда, когда последнее отправленное сообщение стало слишком
    слишком давно. Если программа делает запросы быстрее, чем длится период ожидания.
    период, сообщения keep-alive не отправляются.
    """

    def __init__(self, sock: socket, addr, keep_alive: float = 10.0):
        """См. базовый класс.

        Args:
            sock: Сокет инвертора, который считается подключенным.
            addr: сетевой адрес инвертора.
            keep_alive: Максимальное время, прошедшее с момента последнего сообщения до того, как будет отправлено сообщение keep-alive
                запускается сообщение keep-alive. Значение по умолчанию 11 секунд выбрано таким образом,
                что сообщения keep-alive не будут отправляться, когда статус
                извлекается каждые 10 секунд.
        """
        super().__init__(sock, addr)
        self.keep_alive_period = keep_alive
        self.keep_alive_timer = None

        self._ka_thread = None  # Сохранять соединение
        self._ka_stop = Event()  # Используется для остановки сообщений keep-alive
        self.start_keep_alive()

    def stop_keep_alive(self) -> None:
        """Останавливает периодические сообщения keep-alive

        Блокируется на мгновение, если в данный момент обрабатывается запрос keep-alive.
        """
        if not self._ka_thread:
            return  # Не работает, если уже остановлен
        self._ka_stop.set()
        self._ka_thread.join()
        self._ka_thread = None

    def start_keep_alive(self):
        """Начинает периодически отправлять сообщения keep-alive."""
        if self._ka_thread:
            raise RuntimeError("Keep-alive поток уже существует")
        self._ka_stop.clear()
        self._ka_thread = Thread(target=self._ka_runner, daemon=True)
        self._ka_thread.start()

    def _ka_runner(self):
        """Периодически посылает сигнал keep-alive до тех пор, пока не будет остановлен."""
        while True:
            # Будет остановлено, если время истекло или возвращен False
            stopped = self._ka_stop.wait(timeout=self.keep_alive_period)
            if stopped:
                return
            self.keep_alive()

    def keep_alive(self):
        """Отправляет сообщение keep-alive."""
        # Мы должны вызвать суперкласс, потому что self.send/self.receive
        # вмешиваются в работу демона keep-alive.
        # super().send(b"\x01\x02\x02", b"")  # Сообщение о состоянии
        super().send(b"\x01\x09\x02", b"")  # Неизвестное сообщение
        super().receive()

    def send(self, identifier: bytes, payload: bytes):
        """См. базовый класс."""
        self.stop_keep_alive()
        super().send(identifier, payload)
        self.start_keep_alive()

    def receive(self) -> Tuple[bytes, bytes]:
        """См. базовый класс."""
        self.stop_keep_alive()
        msg = super().receive()
        self.start_keep_alive()
        return msg

    def disconnect(self):
        """См. базовый класс."""
        self.stop_keep_alive()
        super().disconnect()
