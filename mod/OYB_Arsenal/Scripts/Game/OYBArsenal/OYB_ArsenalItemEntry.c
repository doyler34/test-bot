// The catalogue entry an arsenal item belongs to holds its display name, but the
// game keeps that link protected. This only reads it.
modded class SCR_ArsenalItem
{
	SCR_EntityCatalogEntry OYB_GetCatalogEntry()
	{
		return m_EntryParent;
	}
}
