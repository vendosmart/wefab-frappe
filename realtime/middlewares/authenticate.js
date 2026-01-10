const cookie = require("cookie");
const request = require("superagent");
const { get_url } = require("../utils");

const { get_conf } = require("../../node_utils");
const conf = get_conf();

function authenticate_with_frappe(socket, next) {
	let namespace = socket.nsp.name;
	namespace = namespace.slice(1, namespace.length); // remove leading `/`

	if (namespace != get_site_name(socket)) {
		next(new Error("Invalid namespace"));
	}

	// Allow localhost for development testing
	const allowedOrigins = ['localhost', '127.0.0.1'];
	const originHost = get_hostname(socket.request.headers.origin);
	const requestHost = get_hostname(socket.request.headers.host);

	// Skip origin check if origin is localhost (for local development)
	if (originHost && !allowedOrigins.includes(originHost)) {
		if (originHost != requestHost) {
			next(new Error("Invalid origin"));
			return;
		}
	}

	// Allow requests with Authorization header (no cookie required)
	// This is needed for external React/Next.js apps using API keys
	if (!socket.request.headers.cookie && !socket.request.headers.authorization) {
		next(new Error("No cookie or authorization header transmitted."));
		return;
	}

	let cookies = cookie.parse(socket.request.headers.cookie || "");
	let authorization_header = socket.request.headers.authorization;

	// Support auth token from Socket.IO handshake auth (for browser clients)
	if (!authorization_header && socket.handshake.auth && socket.handshake.auth.token) {
		authorization_header = `token ${socket.handshake.auth.token}`;
	}

	// Support auth token from query parameters (alternative for browser clients)
	if (!authorization_header && socket.handshake.query && socket.handshake.query.token) {
		authorization_header = `token ${socket.handshake.query.token}`;
	}

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
		socket.site_name = get_hostname(socket.request.headers["x-frappe-site-name"]);
	} else if (
		conf.default_site &&
		["localhost", "127.0.0.1"].indexOf(get_hostname(socket.request.headers.host)) !== -1
	) {
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
