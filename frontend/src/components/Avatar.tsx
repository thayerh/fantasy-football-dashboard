import { avatarUrl, type LeagueUser } from "../api/client";

/**
 * A member's picture, or their initial.
 *
 * Sleeper members without an uploaded image get their initial, not a stock
 * silhouette that makes twelve people look identical — the default avatar ids
 * are shared, and in this league seven of them covered all twelve members.
 *
 * `name` is what the initial is taken from, and it is passed in rather than
 * derived: the standings table labels a row by its member, the roster rail by
 * its team, and the letter should match whichever one the reader sees.
 *
 * Two sizes, both from the theme: 52px on a card, 32px in a table row or a
 * rail. Nothing in between, because a third size would be a third decision to
 * get wrong.
 */
export function Avatar({
  user,
  name,
  size = "sm",
}: {
  user: Pick<LeagueUser, "avatar_url" | "avatar">;
  name: string | null;
  size?: "sm" | "lg";
}) {
  const classes = size === "lg" ? "avatar" : "avatar avatar--sm";
  const url = avatarUrl(user);
  if (!url) {
    return (
      <span className={`${classes} avatar--initial`} aria-hidden>
        {(name ?? "?").charAt(0).toUpperCase()}
      </span>
    );
  }
  return <img className={classes} src={url} alt="" loading="lazy" />;
}
