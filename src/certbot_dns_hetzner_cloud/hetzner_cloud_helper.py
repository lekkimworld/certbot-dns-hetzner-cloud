from typing import Union

from hcloud import Client
from hcloud.zones import BoundZone, ZoneRecord
from hcloud.zones.domain import CreateZoneRRSetResponse

class HetznerCloudHelper:
    """Helper class to manage Hetzner Cloud DNS records."""

    def __init__(self, api_key: str) -> None:
        self.client = Client(api_key)

    def _ensure_zone(self, zone: Union[str, BoundZone]) -> BoundZone:
        if isinstance(zone, BoundZone):
            return zone
        return self.client.zones.get(zone)

    def delete_txt_record(self, zone: Union[str, BoundZone], name: str, value: str | None = None) -> None:
        """Delete a TXT record or a specific value from it.
        
        If value is provided, only removes that specific value from the record set.
        If value is None, deletes all TXT records with the given name.
        """
        # ensure value is quoted if provided
        if value is not None and not (value.startswith("\"") and value.endswith("\"")):
            value = f'"{value}"'

        # load zone object
        bound_zone = self._ensure_zone(zone)

        # search for an existing TXT record
        query_result = self.client.zones.get_rrset_list(zone=bound_zone, name=name, type="TXT")

        # delete if exists
        if len(query_result.rrsets) > 0:
            if value is None:
                # delete entire rrset
                self.client.zones.delete_rrset(query_result.rrsets[0])
            else:
                # remove only the specific value
                remaining_records = [record for record in query_result.rrsets[0].records if record.value != value]
                self.client.zones.delete_rrset(query_result.rrsets[0])
                
                # recreate with remaining records if any
                if remaining_records:
                    self.client.zones.create_rrset(
                        zone=bound_zone,
                        name=name,
                        type="TXT",
                        records=remaining_records
                    )

    def put_txt_record(self, zone: Union[str, BoundZone], name: str, value: str, comment: str | None = None) -> CreateZoneRRSetResponse:
        """Create or update a TXT record."""
        # ensure value is quoted
        if not (value.startswith("\"") and value.endswith("\"")):
            value = f'"{value}"'

        # load zone object
        bound_zone = self._ensure_zone(zone)

        # check for existing TXT records
        query_result = self.client.zones.get_rrset_list(zone=bound_zone, name=name, type="TXT")
        
        existing_records = []
        if len(query_result.rrsets) > 0:
            # preserve existing records and delete the rrset
            existing_records = [record for record in query_result.rrsets[0].records if record.value != value]
            self.client.zones.delete_rrset(query_result.rrsets[0])

        # create new TXT record with all values (existing + new)
        all_records = existing_records + [ZoneRecord(value=value, comment=comment)]
        
        return self.client.zones.create_rrset(
            zone=bound_zone,
            name=name,
            type="TXT",
            records=all_records
        )