"""
Multimodal Context Management for LCM Protocol
Phase 4: Retrieval, Compression, and Multimodal Support
"""

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json


@dataclass
class ModalityMetadata:
    """Metadata for different modality types"""
    modality_type: str  # 'text', 'image', 'audio', 'video', 'structured'
    encoding: str
    size_bytes: int
    compression_ratio: Optional[float] = None
    format_version: str = "1.0"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'modality_type': self.modality_type,
            'encoding': self.encoding,
            'size_bytes': self.size_bytes,
            'compression_ratio': self.compression_ratio,
            'format_version': self.format_version
        }


@dataclass
class MultimodalContext:
    """Container for multimodal context data"""
    context_id: str
    modalities: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, ModalityMetadata] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def add_modality(self, modality_type: str, content: Any, 
                    encoding: str, size_bytes: int) -> None:
        """Add a modality to the context"""
        self.modalities[modality_type] = content
        self.metadata[modality_type] = ModalityMetadata(
            modality_type=modality_type,
            encoding=encoding,
            size_bytes=size_bytes
        )
        self.updated_at = datetime.utcnow()
    
    def get_modality(self, modality_type: str) -> Optional[Any]:
        """Retrieve a specific modality"""
        return self.modalities.get(modality_type)
    
    def remove_modality(self, modality_type: str) -> bool:
        """Remove a modality from the context"""
        if modality_type in self.modalities:
            del self.modalities[modality_type]
            del self.metadata[modality_type]
            self.updated_at = datetime.utcnow()
            return True
        return False
    
    def get_total_size(self) -> int:
        """Calculate total size across all modalities"""
        return sum(meta.size_bytes for meta in self.metadata.values())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'context_id': self.context_id,
            'modalities': self.modalities,
            'metadata': {k: v.to_dict() for k, v in self.metadata.items()},
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class MultimodalManager:
    """Manages multimodal context operations"""
    
    def __init__(self):
        self.contexts: Dict[str, MultimodalContext] = {}
    
    def create_context(self, context_id: Optional[str] = None) -> MultimodalContext:
        """Create a new multimodal context"""
        if context_id is None:
            context_id = self._generate_context_id()
        
        context = MultimodalContext(context_id=context_id)
        self.contexts[context_id] = context
        return context
    
    def get_context(self, context_id: str) -> Optional[MultimodalContext]:
        """Retrieve a context by ID"""
        return self.contexts.get(context_id)
    
    def delete_context(self, context_id: str) -> bool:
        """Delete a context"""
        if context_id in self.contexts:
            del self.contexts[context_id]
            return True
        return False
    
    def merge_contexts(self, source_id: str, target_id: str, 
                      conflict_strategy: str = 'newest') -> Optional[MultimodalContext]:
        """Merge two contexts with conflict resolution"""
        source = self.contexts.get(source_id)
        target = self.contexts.get(target_id)
        
        if not source or not target:
            return None
        
        for modality_type, content in source.modalities.items():
            if modality_type not in target.modalities:
                # No conflict, add directly
                metadata = source.metadata[modality_type]
                target.add_modality(
                    modality_type, content, 
                    metadata.encoding, metadata.size_bytes
                )
            else:
                # Conflict resolution
                if conflict_strategy == 'newest':
                    if source.updated_at > target.updated_at:
                        metadata = source.metadata[modality_type]
                        target.add_modality(
                            modality_type, content,
                            metadata.encoding, metadata.size_bytes
                        )
                elif conflict_strategy == 'largest':
                    if source.metadata[modality_type].size_bytes > target.metadata[modality_type].size_bytes:
                        metadata = source.metadata[modality_type]
                        target.add_modality(
                            modality_type, content,
                            metadata.encoding, metadata.size_bytes
                        )
        
        return target
    
    def _generate_context_id(self) -> str:
        """Generate a unique context ID"""
        timestamp = datetime.utcnow().isoformat()
        hash_input = f"{timestamp}-{len(self.contexts)}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]
