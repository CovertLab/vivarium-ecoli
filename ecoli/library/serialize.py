import numpy as np
import orjson
import re
from unum import Unum
from vivarium.core.registry import Serializer
from vivarium.library.topology import convert_path_style, normalize_path

from ecoli.library.parameters import Parameter, param_store


class UnumSerializer(Serializer):
    
    def __init__(self):
        super().__init__()
        self.regex_for_serialized = re.compile('!UnumSerializer\\[(.*)\\]')

    python_type = Unum
    def serialize(self, value):
        num = value.asNumber()
        if isinstance(num, np.ndarray):
            num = num.tolist()
        # TODO: Fix this
        # return f'!UnumSerializer[{num} | {value.strUnit()}]'
        return num
        
    def can_deserialize(self, data):
        if not isinstance(data, str):
            return False
        return bool(self.regex_for_serialized.fullmatch(data))

    def deserialize(self, data):
        # WARNING: This deserialization is lossy and drops the unit
        # information since there is no easy way to parse Unum's unit
        # strings.
        matched_regex = self.regex_for_serialized.fullmatch(data)
        if matched_regex:
            data = matched_regex.group(1)
        return orjson.loads(data.split(' | ')[0])


class ParameterSerializer(Serializer):
    
    def __init__(self):
        super().__init__()
        self.regex_for_serialized = re.compile('!ParameterSerializer\\[(.*)\\]')
    
    python_type = Parameter
        
    def can_deserialize(self, data):
        if not isinstance(data, str):
            return False
        return bool(self.regex_for_serialized.fullmatch(data))

    def deserialize(self, data):
        matched_regex = self.regex_for_serialized.fullmatch(data)
        if matched_regex:
            data = matched_regex.group(1)
        path = normalize_path(convert_path_style(data))
        return param_store.get(path)
    

class NumpyRandomStateSerializer(Serializer):
    
    def __init__(self):
        super().__init__()
        self.regex_for_serialized = re.compile('!RandomStateSerializer\\[(.*)\\]')

    python_type = np.random.RandomState
    def serialize(self, value):
        rng_state = list(value.get_state())
        rng_state[1] = rng_state[1].tolist()
        return f'!RandomStateSerializer[{str(tuple(rng_state))}]'
        
    def can_deserialize(self, data):
        if not isinstance(data, str):
            return False
        return bool(self.regex_for_serialized.fullmatch(data))

    def deserialize(self, data):
        matched_regex = self.regex_for_serialized.fullmatch(data)
        if matched_regex:
            data = matched_regex.group(1)
        data = orjson.loads(data)
        rng = np.random.RandomState()
        rng.set_state(data)
        return rng


class MethodSerializer(Serializer):
    """Serializer for bound method objects."""
    python_type = type(ParameterSerializer().deserialize)

    def serialize(self, data):
        return f"!MethodSerializer[{str(data)}]"
