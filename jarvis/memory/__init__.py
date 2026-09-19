"""Memory subsystem — public interface is memory.api (RFC-014). This is
the ONLY package allowed to write to the `memory` table (decision #10,
§4: Memory has exactly one writer)."""
