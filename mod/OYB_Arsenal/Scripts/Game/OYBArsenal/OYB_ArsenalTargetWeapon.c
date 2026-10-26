// Which weapon the arsenal's "Fits" list and attachment fitting work on: the one in
// the player's hands (or the first one carried if the hands are empty), unless
// they've picked another weapon they carry with the Weapon button. Grenades and empty slots are skipped.
class OYB_TargetWeapon
{
	protected static IEntity s_Chosen;

	static IEntity Get(IEntity player)
	{
		if (!player)
			return null;

		if (s_Chosen && GetCarried(player).Contains(s_Chosen))
			return s_Chosen;

		s_Chosen = null;
		IEntity held = GetHeld(player);
		if (held)
			return held;

		// Nothing in hands: the first gun carried, so slung weapons can still be
		// customised.
		array<IEntity> carried = GetCarried(player);
		if (carried.IsEmpty())
			return null;

		return carried[0];
	}

	// Moves to the next (step 1) or previous (step -1) weapon the player carries,
	// starting from the one in use.
	static void Next(IEntity player, int step = 1)
	{
		array<IEntity> carried = GetCarried(player);
		if (carried.IsEmpty())
		{
			s_Chosen = null;
			return;
		}

		int index = carried.Find(Get(player));
		int count = carried.Count();
		s_Chosen = carried[((index + step) % count + count) % count];
	}

	static array<IEntity> GetCarried(IEntity player)
	{
		array<IEntity> carried = {};
		BaseWeaponManagerComponent weapons = BaseWeaponManagerComponent.Cast(player.FindComponent(BaseWeaponManagerComponent));
		if (!weapons)
			return carried;

		array<WeaponSlotComponent> slots = {};
		weapons.GetWeaponsSlots(slots);
		foreach (WeaponSlotComponent slot : slots)
		{
			if (!slot)
				continue;

			IEntity weaponEntity = slot.GetWeaponEntity();
			if (!weaponEntity)
				continue;

			BaseWeaponComponent weapon = BaseWeaponComponent.Cast(weaponEntity.FindComponent(BaseWeaponComponent));
			if (!weapon)
				continue;

			EWeaponType type = weapon.GetWeaponType();
			if (type == EWeaponType.WT_NONE || type == EWeaponType.WT_FRAGGRENADE || type == EWeaponType.WT_SMOKEGRENADE)
				continue;

			carried.Insert(weaponEntity);
		}
		return carried;
	}

	static IEntity GetHeld(IEntity player)
	{
		BaseWeaponManagerComponent weapons = BaseWeaponManagerComponent.Cast(player.FindComponent(BaseWeaponManagerComponent));
		if (!weapons)
			return null;

		BaseWeaponComponent weapon = weapons.GetCurrentWeapon();
		if (!weapon)
			return null;

		WeaponSlotComponent slot = WeaponSlotComponent.Cast(weapon);
		if (slot)
			return slot.GetWeaponEntity();

		return weapon.GetOwner();
	}
}
