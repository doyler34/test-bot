// Every infantry arsenal is a full arsenal: whatever the box was set up for
// (clothing, equipment, weapons...), it offers all of its faction's infantry
// gear. Applied at start and whenever the box's item types are set again (a
// loaded save, the editor, the server telling a player), on server and clients
// alike, so the replicated item types match. Arsenals with no infantry gear at
// all (vehicle pylons, mortars) and switched-off arsenals are left alone.
modded class SCR_ArsenalComponent
{
	protected static const int OYB_INFANTRY_TYPES = SCR_EArsenalItemType.RIFLE | SCR_EArsenalItemType.PISTOL | SCR_EArsenalItemType.MACHINE_GUN | SCR_EArsenalItemType.SNIPER_RIFLE | SCR_EArsenalItemType.ROCKET_LAUNCHER | SCR_EArsenalItemType.LETHAL_THROWABLE | SCR_EArsenalItemType.NON_LETHAL_THROWABLE | SCR_EArsenalItemType.HEAL | SCR_EArsenalItemType.BACKPACK | SCR_EArsenalItemType.HEADWEAR | SCR_EArsenalItemType.TORSO | SCR_EArsenalItemType.VEST_AND_WAIST | SCR_EArsenalItemType.LEGS | SCR_EArsenalItemType.FOOTWEAR | SCR_EArsenalItemType.HANDWEAR | SCR_EArsenalItemType.EQUIPMENT | SCR_EArsenalItemType.WEAPON_ATTACHMENT | SCR_EArsenalItemType.EXPLOSIVES;
	protected static const int OYB_ALWAYS_TYPES = OYB_INFANTRY_TYPES | SCR_EArsenalItemType.RADIO_BACKPACK;
	protected static const int OYB_ALWAYS_MODES = SCR_EArsenalItemMode.DEFAULT | SCR_EArsenalItemMode.WEAPON | SCR_EArsenalItemMode.WEAPON_VARIANTS | SCR_EArsenalItemMode.AMMUNITION | SCR_EArsenalItemMode.CONSUMABLE | SCR_EArsenalItemMode.ATTACHMENT;

	override protected void OnPostInit(IEntity owner)
	{
		m_eSupportedArsenalItemTypes = OYB_WidenTypes(m_eSupportedArsenalItemTypes);
		m_eSupportedArsenalItemModes = OYB_WidenModes(m_eSupportedArsenalItemTypes, m_eSupportedArsenalItemModes);

		super.OnPostInit(owner);
	}

	override void SetSupportedArsenalItemTypes(SCR_EArsenalItemType types)
	{
		super.SetSupportedArsenalItemTypes(OYB_WidenTypes(types));
	}

	override void SetSupportedArsenalItemModes(SCR_EArsenalItemMode modes)
	{
		super.SetSupportedArsenalItemModes(OYB_WidenModes(m_eSupportedArsenalItemTypes, modes));
	}

	override protected void DelayedOnArsenalUpdatedClient(SCR_EArsenalItemType itemTypes, SCR_EArsenalItemMode itemModes)
	{
		int types = OYB_WidenTypes(itemTypes);
		super.DelayedOnArsenalUpdatedClient(types, OYB_WidenModes(types, itemModes));
	}

	protected static int OYB_WidenTypes(int types)
	{
		if (types & OYB_INFANTRY_TYPES)
			return types | OYB_ALWAYS_TYPES;

		return types;
	}

	// No modes at all means the arsenal is switched off.
	protected static int OYB_WidenModes(int types, int modes)
	{
		if (modes && (types & OYB_INFANTRY_TYPES))
			return modes | OYB_ALWAYS_MODES;

		return modes;
	}
}
