// Item names changed in the OYB panel. An item's name lives in the UI info of its
// inventory attributes; every one of those is noted as the game creates it, and
// the ones whose original name matches a renamed item are given the new name.
// Whatever reads the item's name from there (the arsenal, the hover box, the
// inventory) then shows the new one. Done on each player's game; a dedicated
// server shows no names, so it skips this.
//
// Items are matched by their original name, so items that shared a name before
// share the new one too.
class OYB_ItemNames
{
	protected static ref array<UIInfo> s_aInfos = {};
	protected static ref array<string> s_aOriginals = {};
	// Original name -> new name.
	protected static ref map<string, string> s_mRenames = new map<string, string>();
	// Reading an item's original name builds a throwaway copy of its attributes,
	// which mustn't be noted or renamed.
	protected static bool s_bReading;

	static void Note(UIInfo info)
	{
		if (!info || s_bReading || System.IsConsoleApp() || s_aInfos.Contains(info))
			return;

		string original = info.GetName();
		s_aInfos.Insert(info);
		s_aOriginals.Insert(original);

		string renamed;
		if (s_mRenames.Find(original, renamed))
			info.SetName(renamed);
	}

	// prefab -> new name, from the panel's settings.
	static void Set(notnull map<ResourceName, string> renames)
	{
		s_mRenames.Clear();
		foreach (ResourceName prefab, string name : renames)
		{
			string original = ReadOriginal(prefab);
			if (!original.IsEmpty())
				s_mRenames.Set(original, name);
		}

		if (System.IsConsoleApp())
			return;

		for (int i = s_aInfos.Count() - 1; i >= 0; i--)
		{
			UIInfo info = s_aInfos[i];
			if (!info)
			{
				s_aInfos.Remove(i);
				s_aOriginals.Remove(i);
				continue;
			}

			string renamed;
			if (s_mRenames.Find(s_aOriginals[i], renamed))
				info.SetName(renamed);
			else
				info.SetName(s_aOriginals[i]);
		}
	}

	// The name as the item's mod gives it, before any rename (often a
	// translation key like "#AR-Weapon_M16A2_Name").
	static string ReadOriginal(ResourceName prefab)
	{
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return string.Empty;

		IEntityComponentSource itemSource = SCR_ComponentHelper.GetInventoryItemComponentSource(SCR_BaseContainerTools.FindEntitySource(resource));
		if (!itemSource)
			return string.Empty;

		s_bReading = true;
		SCR_ItemAttributeCollection attributes = SCR_ComponentHelper.GetInventoryItemInfo(itemSource);
		s_bReading = false;
		if (!attributes || !attributes.GetUIInfo())
			return string.Empty;

		return attributes.GetUIInfo().GetName();
	}
}

modded class SCR_ItemAttributeCollection
{
	override protected void OnInitCollection(IEntityComponentSource src)
	{
		super.OnInitCollection(src);
		OYB_ItemNames.Note(GetUIInfo());
	}
}
