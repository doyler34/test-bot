// Follows one mortar round from the tube to where it lands, then says where
// that was: on screen, and as one OYB_MORTAR line in the log that the OYB
// mortar map's Test shots box reads.
//
// While a round sits in someone's kit or an ammo box it barely moves; once it's
// going faster than any person can, it's been fired, and the last place it was
// seen before it disappeared is where it went off.
class OYB_ShotTracker
{
	// Metres per second. A sprinting soldier is under 10, a mortar round leaves at well over 100.
	protected static const float FIRED_SPEED = 40;
	// Loading a round moves it to the tube in one jump, then it sits still. A fired
	// round is still going far faster than this a second after it leaves.
	protected static const float SETTLED_SPEED = 20;

	protected vector m_vPrevious;
	protected vector m_vLaunch;
	protected vector m_vEarly;
	protected bool m_bEarly;
	protected vector m_vLast;
	protected float m_fFlight;
	protected bool m_bStarted;
	protected bool m_bFlying;
	protected bool m_bReported;

	void Step(vector now, float timeSlice)
	{
		if (!m_bStarted)
		{
			m_bStarted = true;
			m_vPrevious = now;
		}
		float speed = 0;
		if (timeSlice > 0)
			speed = vector.Distance(now, m_vPrevious) / timeSlice;
		if (!m_bFlying)
		{
			if (speed > FIRED_SPEED)
			{
				m_bFlying = true;
				m_vLaunch = m_vPrevious;
				m_bEarly = false;
				m_fFlight = timeSlice;
			}
		}
		else
		{
			m_fFlight += timeSlice;
			if (m_fFlight < 1 && speed < SETTLED_SPEED)
				m_bFlying = false;
			// A quarter of a second out it has cleared the tube and is on its real path.
			if (!m_bEarly && m_fFlight >= 0.25)
			{
				m_bEarly = true;
				m_vEarly = now;
			}
		}
		m_vPrevious = now;
		m_vLast = now;
	}

	void Finish()
	{
		if (!m_bFlying || m_bReported)
			return;
		m_bReported = true;

		float east = m_vLast[0] - m_vLaunch[0];
		float north = m_vLast[2] - m_vLaunch[2];
		float range = Math.Sqrt(east * east + north * north);
		float bearing = Math.Atan2(east, north) * Math.RAD2DEG;
		if (bearing < 0)
			bearing += 360;

		// The angle it left the tube at, from where it was a quarter of a second out.
		vector step = m_vEarly - m_vLaunch;
		if (!m_bEarly)
			step = m_vLast - m_vLaunch;
		float flat = Math.Sqrt(step[0] * step[0] + step[2] * step[2]);
		float launch = Math.Atan2(step[1], flat) * Math.RAD2DEG;

		string line = string.Format("OYB_MORTAR from=%1,%2,%3 to=%4,%5,%6 range=%7 bearing=%8 launch=%9",
			Whole(m_vLaunch[0]), Whole(m_vLaunch[2]), Whole(m_vLaunch[1]),
			Whole(m_vLast[0]), Whole(m_vLast[2]), Whole(m_vLast[1]),
			Whole(range), Tenths(bearing), Tenths(launch));
		line += " flight=" + Tenths(m_fFlight);
		PrintFormat("%1", line);

		string grid = string.Format("%1 %2", Whole(m_vLast[0]), Whole(m_vLast[2]));
		string text = string.Format("Landed at %1 (height %2 m)\n%3 m from where it was fired, bearing %4°\nLeft the tube at %5°, %6 s in the air",
			grid, Whole(m_vLast[1]), Whole(range), Tenths(bearing), Tenths(launch), Tenths(m_fFlight));
		SCR_HintManagerComponent.ShowCustomHint(text, "Mortar test", 20);
	}

	protected string Whole(float value)
	{
		return Math.Round(value).ToString();
	}

	protected string Tenths(float value)
	{
		return (Math.Round(value * 10) / 10).ToString();
	}
}
