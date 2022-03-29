from collections import OrderedDict
from decimal import Decimal


class StatusType:
    """Тип значения состояния, которое может появиться в данных состояния."""

    def get_value(self, status_format, status_payload):
        """Возвращает значение для данного типа статуса.

        Args:
            status_format: Строка байтов формата состояния, предоставленная преобразователем.
                преобразователем.
            status_payload: Байт-строка данных состояния, предоставленная преобразователем.
                преобразователем.

        Returns:
            Значение для данного типа состояния или None, если значение отсутствует.
        """
        raise NotImplementedError("Abstract method")


class BytesStatusType(StatusType):
    """Получает байты в заданных позициях ID типа."""

    def __init__(self, *type_ids):
        """Конструктор.

        Args:
            *type_ids: Идентификаторы типов, которые мы ищем в формате
                строка. Если они найдены, данные полезной нагрузки в этом месте
                возвращаются. Несколько значений идентификаторов объединяются.
        """
        self.type_ids = type_ids

    def get_value(self, status_format, status_payload):
        """См. базовый класс."""
        indices = [status_format.find(type_id) for type_id in self.type_ids]
        if -1 in indices:
            return None
        values = [status_payload[i * 2:i * 2 + 2] for i in indices]
        return b''.join(values)


class IntStatusType(BytesStatusType):
    """Возвращает значение в виде целого числа."""

    def __init__(self, *type_ids, signed=False):
        """Конструктор.

        Args:
            *type_ids: Идентификаторы типов, см. суперкласс.
            signed: Интерпретируются ли байты как целое число без знака или как
                как целое число без знака или как целое число с двумя знаками.
        """
        """См. BytesStatusType для позиционных аргументов, signed указывает, является ли значение статуса знаковым."""
        super().__init__(*type_ids)
        self.signed = signed

    def get_value(self, status_format, status_payload):
        """См. базовый класс."""
        sequence = super().get_value(status_format, status_payload)
        if sequence is None:
            return None
        return int.from_bytes(sequence, byteorder='big', signed=self.signed)


class DecimalStatusType(IntStatusType):
    """Тип состояния, который масштабирует результат и возвращает десятичное значение."""

    def __init__(self, *type_ids, scale: int = 0, signed: bool = False):
        """Конструктор.

        Args:
            *type_ids: Идентификатор типа данных.
            scale: Способ масштабирования (целочисленного) значения, возвращаемого преобразователем.
                Результат: <значение инвертора>*10^scale.
            signed: Является ли значение знаковым.
        """
        super().__init__(*type_ids, signed=signed)
        self.scale = scale

    def get_value(self, status_format, status_payload):
        """См. базовый класс."""
        int_val = super().get_value(status_format, status_payload)
        if int_val is None:
            return None
        return Decimal(int_val).scaleb(self.scale)


class OperationModeStatusType(IntStatusType):
    """Возвращает режим работы в виде строки.

    Значение одно из Wait, Normal, Fault, Permanent fault, Check или PV power
    off. Это соответствует значению, отображаемому в SolarPower Browser V3.
    """

    def __init__(self):
        """Конструктор."""
        super().__init__(0x0c)

    def get_value(self, status_format, status_payload):
        """См. базовый класс."""
        int_val = super().get_value(status_format, status_payload)
        operating_modes = {0: 'Wait', 1: 'Normal', 2: 'Fault', 3: 'Permanent fault', 4: 'Check', 5: 'PV power off'}
        return operating_modes[int_val]


class OneOfStatusType(StatusType):
    """Возвращает значение первого значения типа статуса not-None.

    Может использоваться в случае, когда существует несколько идентификаторов типа, которые ссылаются на
    один и тот же тип состояния и являются взаимоисключающими.
    """

    def __init__(self, *status_types: StatusType):
        """Конструктор.

        Args:
            *status_types: Список типов статусов для проверки значения.
        """
        self.status_types = status_types

    def get_value(self, status_format, status_payload):
        """См. базовый класс."""
        for status_type in self.status_types:
            val = status_type.get_value(status_format, status_payload)
            if val is not None:
                return val
        return None


class IfPresentStatusType(BytesStatusType):
    """Фильтрует тип состояния на основании наличия идентификатора другого типа."""

    def __init__(self, type_id, presence, status_type):
        """Конструктор.

        Args:
            type_id: Идентификатор типа для проверки присутствия.
            presence: Должен ли идентификатор типа присутствовать (True) или не должен присутствовать (False).
                присутствовать (False).
            status_type: Значение типа состояния, которое будет возвращено, если
                вышеуказанный идентификатор типа присутствует.
        """
        super().__init__(type_id)
        self.presence = presence
        self.status_type = status_type

    def get_value(self, status_format, status_payload):
        """См. базовый класс."""
        actual_presence = super().get_value(status_format, status_payload) is not None
        if self.presence == actual_presence:
            return self.status_type.get_value(status_format, status_payload)
        return None


status_types = OrderedDict(
    operation_mode=OperationModeStatusType(),
    total_operation_time=IntStatusType(0x09, 0x0a),
    pv1_input_power=DecimalStatusType(0x27),
    pv2_input_power=DecimalStatusType(0x28),
    pv1_voltage=DecimalStatusType(0x01, scale=-1),
    pv2_voltage=DecimalStatusType(0x02, scale=-1),
    pv1_current=DecimalStatusType(0x04, scale=-1),
    pv2_current=DecimalStatusType(0x05, scale=-1),
    output_power=OneOfStatusType(DecimalStatusType(0x0b), DecimalStatusType(0x34)),
    energy_today=DecimalStatusType(0x11, scale=-2),
    energy_total=OneOfStatusType(DecimalStatusType(0x07, 0x08, scale=-1), DecimalStatusType(0x35, 0x36, scale=-1)),
    grid_voltage=IfPresentStatusType(0x51, False, DecimalStatusType(0x32, scale=-1)),
    grid_current=IfPresentStatusType(0x51, False, DecimalStatusType(0x31, scale=-1)),
    grid_frequency=IfPresentStatusType(0x51, False, DecimalStatusType(0x33, scale=-2)),
    grid_voltage_r_phase=IfPresentStatusType(0x51, True, DecimalStatusType(0x32, scale=-1)),
    grid_current_r_phase=IfPresentStatusType(0x51, True, DecimalStatusType(0x31, scale=-1)),
    grid_frequency_r_phase=IfPresentStatusType(0x51, True, DecimalStatusType(0x33, scale=-2)),
    grid_voltage_s_phase=DecimalStatusType(0x52, scale=-1),
    grid_current_s_phase=DecimalStatusType(0x51, scale=-1),
    grid_frequency_s_phase=DecimalStatusType(0x53, scale=-2),
    grid_voltage_t_phase=DecimalStatusType(0x72, scale=-1),
    grid_current_t_phase=DecimalStatusType(0x71, scale=-1),
    grid_frequency_t_phase=DecimalStatusType(0x73, scale=-2),
    internal_temperature=DecimalStatusType(0x00, signed=True, scale=-1),
    heatsink_temperature=DecimalStatusType(0x2f, signed=True, scale=-1),
)
