const cookie = require("cookie");
const request = require("superagent");
const { get_url } = require("../utils");

const { get_conf } = require("../../node_utils");
const conf = get_conf();

// Get allowed origins from config (comma-separated string or array)
function get_allowed_origins() {
	let allowed = conf.socketio_allowed_origins || [];
	if (typeof allowed === "string") {
		allowed = allowed.split(",").map((o) => o.trim().toLowerCase());
	}
	return allowed;
}

function is_origin_allowed(origin_hostname, host_hostname, real_origin_hostname) {
	// Same origin is always allowed
	if (origin_hostname === host_hostname) {
		return true;
	}

	// Check against allowed origins list from config
	const allowed_origins = get_allowed_origins();
	if (allowed_origins.length > 0) {
		// Check real origin (passed via X-Real-Origin header from nginx) against allowed list
		const origin_to_check = real_origin_hostname || origin_hostname;
		return allowed_origins.some((allowed) => {
			// Support wildcard patterns like *.example.com
			if (allowed.startsWith("*")) {
				const suffix = allowed.slice(1); // e.g., ".example.com"
				return origin_to_check && origin_to_check.endsWith(suffix);
			}
			return origin_to_check && origin_to_check === allowed.toLowerCase();
		});
	}

	return false;
}

function authenticate_with_frappe(socket, next) {
	let namespace = socket.nsp.name;
	namespace = namespace.slice(1, namespace.length); // remove leading `/`

	if (namespace != get_site_name(socket)) {
		next(new Error("Invalid namespace"));
		return;
	}

	const origin_hostname = get_hostname(socket.request.headers.origin);
	const host_hostname = get_hostname(socket.request.headers.host);
	const real_origin_hostname = get_hostname(socket.request.headers["x-real-origin"]);

	if (!is_origin_allowed(origin_hostname, host_hostname, real_origin_hostname)) {
		next(new Error("Invalid origin"));
		return;
	}

	let cookies = cookie.parse(socket.request.headers.cookie || "");
	let authorization_header = socket.request.headers.authorization;

	// Allow connection if either cookie or authorization header is present
	// (removed strict cookie requirement for external clients using auth headers)

	if (!cookies.sid && !authorization_header) {
		next(new Error("No authentication method used. Use cookie or authorization header."));
		return;
	}

	let auth_req = request.get(get_url(socket, "/api/method/frappe.realtime.get_user_info"));
	if (authorization_header) {
		auth_req = auth_req.set("Authorization", authorization_header);
	} else if (cookies.sid) {
		auth_req = auth_req.query({ sid: cookies.sid });
	}

	auth_req
		.type("form")
		.then((res) => {
			socket.user = res.body.message.user;
			socket.user_type = res.body.message.user_type;
			socket.sid = cookies.sid;
			socket.authorization_header = authorization_header;
			next();
		})
		.catch((e) => {
			next(new Error(`Unauthorized: ${e}`));
		});
}

function get_site_name(socket) {
	if (socket.site_name) {
		return socket.site_name;
	} else if (socket.request.headers["x-frappe-site-name"]) {
		// External clients should use this header to specify the target site
		socket.site_name = get_hostname(socket.request.headers["x-frappe-site-name"]);
	} else if (conf.default_site) {
		// Use default site from config (useful for single-site setups and external clients)
		socket.site_name = conf.default_site;
	} else if (socket.request.headers.origin) {
		socket.site_name = get_hostname(socket.request.headers.origin);
	} else {
		socket.site_name = get_hostname(socket.request.headers.host);
	}
	return socket.site_name;
}

function get_hostname(url) {
	if (!url) return undefined;
	if (url.indexOf("://") > -1) {
		url = url.split("/")[2];
	}
	return url.match(/:/g) ? url.slice(0, url.indexOf(":")) : url;
}

module.exports = authenticate_with_frappe;
